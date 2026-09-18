import uvicorn
from fastapi import FastAPI, UploadFile, File, Depends, BackgroundTasks, HTTPException
from starlette.middleware.cors import CORSMiddleware
import os
from fastapi.responses import FileResponse
from starlette.staticfiles import StaticFiles

from knowledge.api.auth_router import register_auth_routes
from knowledge.core.deps import get_import_file_service
from knowledge.service.file_import_service import ImportFileService
from knowledge.schema.auth_schema import AuthUser
from knowledge.schema.upload_schema import UploadResponse,TaskStatusResponse
from knowledge.core.paths import get_web_dist_dir
from knowledge.utils.auth_util import require_auth_user
from knowledge.utils.task_util import TASK_STATUS_CANCELLED, cancel_task, get_task_info, get_task_status


def _frontend_page() -> FileResponse:
  web_index_path = os.path.join(get_web_dist_dir(), "index.html")
  if os.path.exists(web_index_path):
    return FileResponse(web_index_path)
  raise HTTPException(status_code=404, detail="Vue frontend build not found. Run `npm run build` in knowledge/web.")


def register_router(app):
  """
    1.文件上传处理 由业务层处理 需要创建业务层类及对象 采用依赖注入的方式创建业务层对象 并且单例创建 缓存重复利用
    2.异步启动langgraph流程
  """
  register_auth_routes(app)

  @app.get("/")
  def index_page():
    return _frontend_page()

  @app.get("/import")
  def import_page():
    return _frontend_page()

  @app.get("/chat")
  def chat_page():
    return _frontend_page()

  @app.get("/front/{legacy_path:path}")
  def legacy_front_page(legacy_path: str):
    return _frontend_page()



  # 用装饰器把 URL 挂到 app 上：请求路径 → 处理函数
  @app.post("/upload",response_model=UploadResponse)  # POST /upload：接 multipart 表单里的上传文件，封装成 UploadFile
  async def upload_file(
      background_tasks: BackgroundTasks,
      service:ImportFileService = Depends(get_import_file_service),  #依赖注入
      current_user: AuthUser = Depends(require_auth_user),
      file:UploadFile = File(...),):
    task_id,file_dir,import_file_path = service.upload_file(file)
    # 同步启动langgraph流程 用户需要等待流程完成才可以返回结果给前端
    # service.run_import_graph(task_id,file_dir,import_file_path) 

    # 异步启动流程
    background_tasks.add_task(service.run_import_graph, task_id, file_dir, import_file_path)





    return UploadResponse(task_id=task_id,message="文件上传处理成功！")



  @app.get("/status/{task_id}",response_model=TaskStatusResponse)  # 路径里的 {task_id} 自动注入同名函数参数
  async def get_status(
      task_id:str,
      current_user: AuthUser = Depends(require_auth_user),
  ):
    # 从任务追踪工具里读取该任务的全局信息（状态/运行中节点/已完成节点/各节点耗时）
    task_info = get_task_info(task_id)
    return TaskStatusResponse(**task_info)


  @app.post("/cancel/{task_id}")
  async def cancel_import_task(
      task_id: str,
      current_user: AuthUser = Depends(require_auth_user),
  ):
    if not cancel_task(task_id):
      raise HTTPException(status_code=404, detail="任务不存在或已结束")
    return {
      "message": "任务已取消",
      "task_id": task_id,
      "status": get_task_status(task_id) or TASK_STATUS_CANCELLED,
    }
  





# 应用工厂：每次调用新建一个 FastAPI 实例并注册好路由，方便测试和多处复用
def create_app():
  app = FastAPI(description='知识库导入',version='v1.0.0')  # 描述/版本显示在自动生成的 /docs 页面
  app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],     # 允许所有来源访问
    allow_credentials=False, # 如果为True 另外三个不能为*
    allow_methods=["*"],     # 允许所有请求方法
    allow_headers=["*"],     # 允许所有请求头
  )
  web_assets_dir = os.path.join(get_web_dist_dir(), "assets")
  if os.path.exists(web_assets_dir):
    app.mount("/assets",StaticFiles(directory=web_assets_dir))


  register_router(app)
  return app



if __name__ == '__main__':
    # 直接运行本文件时启动开发服务器：0.0.0.0=监听所有网卡，端口 8000
    uvicorn.run(app=create_app(), host='0.0.0.0', port=8000,log_level='info')
