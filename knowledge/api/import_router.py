import uvicorn
from fastapi import FastAPI, UploadFile, File, Depends, BackgroundTasks
from starlette.middleware.cors import CORSMiddleware
import os
from fastapi.responses import FileResponse
from starlette.staticfiles import StaticFiles

from knowledge.core.deps import get_import_file_service
from knowledge.service.file_import_service import ImportFileService
from knowledge.schema.upload_schema import UploadResponse,TaskStatusResponse
from knowledge.core.paths import get_front_page_dir
from knowledge.utils.task_util import get_task_info


def register_router(app):
  """
    1.文件上传处理 由业务层处理 需要创建业务层类及对象 采用依赖注入的方式创建业务层对象 并且单例创建 缓存重复利用
    2.异步启动langgraph流程
  """
  @app.get("/import")
  def import_page():
    return FileResponse(path=os.path.join(get_front_page_dir(),"import.html"))



  # 用装饰器把 URL 挂到 app 上：请求路径 → 处理函数
  @app.post("/upload",response_model=UploadResponse)  # POST /upload：接 multipart 表单里的上传文件，封装成 UploadFile
  async def upload_file(
      background_tasks: BackgroundTasks,
      service:ImportFileService = Depends(get_import_file_service),  #依赖注入
      file:UploadFile = File(...),):
    task_id,file_dir,import_file_path = service.upload_file(file)
    # 同步启动langgraph流程 用户需要等待流程完成才可以返回结果给前端
    # service.run_import_graph(task_id,file_dir,import_file_path) 

    # 异步启动流程
    background_tasks.add_task(service.run_import_graph, task_id, file_dir, import_file_path)





    return UploadResponse(task_id=task_id,message="文件上传处理成功！")



  @app.get("/status/{task_id}",response_model=TaskStatusResponse)  # 路径里的 {task_id} 自动注入同名函数参数
  async def get_status(task_id:str):
    # 从任务追踪工具里读取该任务的全局信息（状态/运行中节点/已完成节点/各节点耗时）
    task_info = get_task_info(task_id)
    return TaskStatusResponse(**task_info)
  





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
  front_page_dir = get_front_page_dir()
  if front_page_dir and os.path.exists(front_page_dir):
    app.mount("/front",StaticFiles(directory=front_page_dir))


  register_router(app)
  return app



if __name__ == '__main__':
    # 直接运行本文件时启动开发服务器：0.0.0.0=监听所有网卡，端口 8000
    uvicorn.run(app=create_app(), host='0.0.0.0', port=8000,log_level='info')