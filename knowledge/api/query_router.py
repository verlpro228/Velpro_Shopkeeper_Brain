import os
import asyncio
import uvicorn
from fastapi import FastAPI, BackgroundTasks, HTTPException, Request, Depends
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from knowledge.core.paths import get_front_page_dir
from knowledge.core.deps import get_query_service
from knowledge.schema.query_schema import QueryRequest, QueryResponse, StreamSubmitResponse
from knowledge.service.query_service import QueryService
from knowledge.utils.sse_util import sse_generator, create_sse_queue, get_sse_queue
from knowledge.utils.task_util import get_task_status, TASK_STATUS_FAILED, clear_task
from knowledge.processor.query_process.base import setup_logging

# 非流式请求整体超时：覆盖完整查询流程（检索+LLM 生成），超时说明下游卡死
QUERY_TIMEOUT_SECONDS = 120


def create_app() -> FastAPI:
  app = FastAPI(title="Query Service", description="知识库查询服务")
  app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
  )
  # http://localhost:8001/front/chat.html
  front_page_dir = get_front_page_dir()
  if front_page_dir and os.path.exists(front_page_dir):
    app.mount("/front", StaticFiles(directory=front_page_dir))
  register_routes(app)
  return app



def register_routes(app: FastAPI):

  @app.get("/chat")
  async def chat_page():
    path = os.path.join(get_front_page_dir(), "chat.html")
    if not os.path.exists(path):
      raise HTTPException(status_code=404, detail="chat.html not found")
    return FileResponse(path)

  @app.post("/query", response_model=QueryResponse | StreamSubmitResponse)
  async def query(
    request: QueryRequest,
    background_tasks: BackgroundTasks,
    service: QueryService = Depends(get_query_service),
  ):

    # 1. 获取session_id
    session_id = request.session_id or service.generate_session_id()
    original_query = request.query
    is_stream = request.is_stream
    # 2. 获取任务_id
    task_id = service.generate_task_id()

    # 3. 开启流式
    if is_stream:
      # 3.1 必须在返回响应前创建队列，否则前端请求 /stream 时队列不存在
      create_sse_queue(task_id)

      # 3.2 后台运行graph
      background_tasks.add_task(
        service.run_query_graph, task_id, session_id,original_query, True
      )

      # 3.3 返回响应
      return StreamSubmitResponse(
        message="Query submitted", session_id=session_id, task_id=task_id
      )

    # 4. 非流式：丢到线程池[默认]避免阻塞事件循环 ； None 表示使用默认的 ThreadPoolExecutor
    loop = asyncio.get_running_loop()
    try:
      # 带超时等待：流程卡死（检索/LLM 无响应）时不能让请求永久挂起
      await asyncio.wait_for(
        loop.run_in_executor(
          None, service.run_query_graph, task_id, session_id, request.query, False
        ),
        timeout=QUERY_TIMEOUT_SECONDS,
      )
    except asyncio.TimeoutError:
      raise HTTPException(status_code=504, detail="查询超时，请稍后重试")

    # 5. 校验任务状态：失败时明确报错，而不是返回空答案伪装成功
    failed = get_task_status(task_id) == TASK_STATUS_FAILED
    # 6. 获取答案（必须在 clear_task 之前读取）
    answer = service.get_answer(task_id)
    # 7. 非流式结果已全部取走，任务字典不再有读者，立即清理防内存泄漏
    clear_task(task_id)

    if failed:
      raise HTTPException(status_code=500, detail="查询流程执行失败，请稍后重试")

    # 8. 返回答案
    return QueryResponse(message="处理完成", session_id=session_id, answer=answer)

  @app.get("/stream/{task_id}")
  async def stream(task_id: str, request: Request) -> StreamingResponse:
    # 队列不存在说明任务不存在或 SSE 已被消费/清理过，明确 404 而不是返回空流
    if get_sse_queue(task_id) is None:
      raise HTTPException(status_code=404, detail="任务不存在或已结束")
    return StreamingResponse(
      sse_generator(task_id, request),
      media_type="text/event-stream",
      # no-cache 防浏览器缓存；X-Accel-Buffering 防 nginx 等代理缓冲导致打字机效果失效
      headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

  @app.get("/history/{session_id}")
  async def get_history(
      session_id: str, limit: int = 50,
      service: QueryService = Depends(get_query_service),
  ):
    try:
      # 上限保护：防止 limit 传超大值一次拖垮 Mongo
      limit = min(limit, 200)
      items = service.get_history(session_id, limit)
      return {"session_id": session_id, "items": items}
    except Exception as e:
      raise HTTPException(status_code=500, detail=f"history error: {e}")

  @app.delete("/history/{session_id}")
  async def clear_chat_history(
    session_id: str,
    service: QueryService = Depends(get_query_service),
  ):
    count = service.clear_history(session_id)
    return {"message": "History cleared", "deleted_count": count}


if __name__ == "__main__":
  setup_logging()
  # 注意：任务状态/SSE 队列均存于进程内存，必须单 worker 运行（uvicorn.run 默认单进程）。
  # 若改用 --workers N 或 gunicorn 多进程，/stream 会连到没有队列的进程导致 404，
  # 任务状态查询也会因进程隔离而查不到结果。
  uvicorn.run(app=create_app(), host="0.0.0.0", port=8001)
