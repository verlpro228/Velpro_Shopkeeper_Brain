"""查询业务服务"""

import uuid
import logging
from typing import List, Dict, Any

from knowledge.processor.query_process.main_graph import query_app
from knowledge.utils.sse_util import push_sse_event, SSEEvent
from knowledge.utils.task_util import update_task_status, get_task_result, clear_task, \
  TASK_STATUS_PROCESSING, TASK_STATUS_COMPLETED, TASK_STATUS_FAILED

logger = logging.getLogger(__name__)


class QueryService:

  def generate_session_id(self) -> str:
    return str(uuid.uuid4())

  def generate_task_id(self) -> str:
    return str(uuid.uuid4())

  def run_query_graph(self, task_id: str, session_id: str, user_query: str, is_stream: bool):
    """执行 LangGraph 查询流程。
    注意：流式模式的 SSE 队列由路由层在调用前创建。
    """

    # 1. 更新任务状态
    update_task_status(task_id, TASK_STATUS_PROCESSING)

    try:
      # 2. 构建初始状态
      default_state = {
        "original_query": user_query,
        "session_id": session_id,
        "task_id": task_id,
        "is_stream": is_stream,
      }
      # 3. 执行查询图谱
      query_app.invoke(default_state)

      # 4. 成功 → 标记完成
      update_task_status(task_id, TASK_STATUS_COMPLETED)

      # 5. 流式模式下 FINAL 事件已由 answer_output 节点推入 SSE 队列，任务字典
      #    此刻已无读者（流式前端不轮询 /status，只消费队列），立即清理防内存泄漏。
      #    非流式模式由路由层取走答案后统一清理，这里不清理。
      if is_stream:
        clear_task(task_id)

    except Exception as e:
      logger.error(f"查询流程执行失败: {e}", exc_info=True)
      # 6. 失败 → 标记失败
      update_task_status(task_id, TASK_STATUS_FAILED)
      # 7. 失败兜底：流式模式必须补推 FINAL，否则前端收不到终止事件会永久等待
      #    非流式没有 SSE 队列，push_sse_event 查不到队列会静默跳过，无副作用
      push_sse_event(task_id, SSEEvent.FINAL, {"answer": "抱歉，服务出现异常，请稍后重试。"})
      # 8. 失败兜底事件已入队，任务字典同样无读者，流式模式一并清理
      if is_stream:
        clear_task(task_id)

  def get_answer(self, task_id: str) -> str:
    return get_task_result(task_id, "answer", "")

  def get_history(self, session_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    from knowledge.utils.mongo_history_util import get_recent_messages
    records = get_recent_messages(session_id, limit=limit)
    return [
      {
        "_id": str(r.get("_id", "")),
        "session_id": r.get("session_id", ""),
        "role": r.get("role", ""),
        "text": r.get("text", ""),
        "rewritten_query": r.get("rewritten_query", ""),
        "item_names": r.get("item_names", []),
        "ts": r.get("ts"),
      }
      for r in records
    ]

  def clear_history(self, session_id: str) -> int:
    from knowledge.utils.mongo_history_util import clear_history
    return clear_history(session_id)
