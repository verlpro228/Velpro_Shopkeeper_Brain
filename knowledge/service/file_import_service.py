import datetime
import logging
import os
import shutil
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile

from knowledge.core.paths import get_local_base_dir
from knowledge.processor.import_process.config import get_config
from knowledge.processor.import_process.exceptions import FileProcessingError, ImportCancelledError, MinioError
from knowledge.processor.import_process.main_graph import kb_import_graph_app
from knowledge.utils.client.storage_clients import StorageClients
from knowledge.utils.task_util import add_running_task, add_done_task, TASK_STATUS_PROCESSING, update_task_status, \
    TASK_STATUS_COMPLETED, TASK_STATUS_FAILED, TASK_STATUS_CANCELLED, get_task_status

logger = logging.getLogger(__name__)

# 上传校验常量：后缀白名单必须与 entry 节点支持的格式保持一致，
# 在上传阶段就拦截非法文件，避免任务创建后到导入流程才报 FAILED
ALLOWED_SUFFIXES = {".pdf", ".md"}
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB，防止超大文件打爆磁盘与下游转换流程

class ImportFileService:
    """
    处理上传文件业务层类
    """
    def upload_file(self, file:UploadFile):
        """
        完成文件上传业务处理。  双写： 保存到本地  and  保存minio
        """

        #0.上传前置校验（文件名安全/类型白名单/大小上限），失败抛 400，不产生任务不落盘
        safe_filename = self._validate_upload(file)

        #1.生成task_id
        task_id = self._generate_task_id()
        update_task_status(task_id, TASK_STATUS_PROCESSING)

        #2.文件上传存储路径    D:\PyProjects\shopkeeper_brain\knowledge\temp_data\20260913
        date_path = self._get_date_path(get_local_base_dir())
        #   D:\PyProjects\shopkeeper_brain\knowledge\temp_data\20260913\absdfdre
        file_dir = os.path.join(date_path, task_id)
        # 不在创建，在忽略
        os.makedirs(file_dir, exist_ok=True)

        add_running_task(task_id,"upload_file")  # 名称与task_util.py中 英文转中文名称一致。

        #3.双写
        #3.1 保存到本地
        #D:\PyProjects\shopkeeper_brain\knowledge\temp_data\20260913\absdfdre\万用表RS-12的使用.pdf
        import_file_path = self._upload_file_to_local(file, file_dir)

        #3.2 保存到minio     file.name用于minio存储子路径名称（用净化后的文件名，防路径成分混入对象key）
        #   http://127.0.0.1:9000/knowledge-base-files/origin_files/20260913/万用表RS-12的使用.pdf
        self._upload_file_to_minio(import_file_path,safe_filename)

        add_done_task(task_id,"upload_file")

        return task_id,file_dir,import_file_path


    def run_import_graph(self, task_id, file_dir, import_file_path):
        """
        启动导入流程 langgraph
        """
        init_state = {
            "task_id": task_id,
            "import_file_path": import_file_path,
            "file_dir": file_dir,  # 导入(出)文件目录
        }
        if get_task_status(task_id) != TASK_STATUS_CANCELLED:
            update_task_status(task_id, TASK_STATUS_PROCESSING)
        try:
            for event in kb_import_graph_app.stream(init_state):  # stream_mode="updates"  增量结果
                if get_task_status(task_id) == TASK_STATUS_CANCELLED:
                    raise ImportCancelledError("任务已取消")
                for node_name, process_state in event.items():
                    print(f"{task_id}-运行节点: {node_name}")
            if get_task_status(task_id) != TASK_STATUS_CANCELLED:
                update_task_status(task_id, TASK_STATUS_COMPLETED)
        except ImportCancelledError:
            update_task_status(task_id, TASK_STATUS_CANCELLED)
            logger.info(f"导入流程已取消: {task_id}")
        except Exception as e:
            if get_task_status(task_id) == TASK_STATUS_CANCELLED:
                logger.info(f"导入流程已取消: {task_id}")
            else:
                update_task_status(task_id, TASK_STATUS_FAILED)
                logger.info(f"导入流程执行出错: {e}")


    def _validate_upload(self, file: UploadFile) -> str:
        """上传前置校验：文件名安全 + 扩展名白名单 + 大小上限。

        逐项校验、快速失败，全部通过才允许创建任务与落盘：
        1. 文件名非空且不含路径成分（Path.name 不等于原名说明带了 / \\ 等分隔符，
           防止 ../../ 目录穿越把文件写到 task 目录之外）
        2. 后缀必须在白名单内（与 entry 节点支持的 .pdf/.md 一致）
        3. 大小非 0 且不超过上限（先 seek 到文件尾读 size，再复位读指针，
           保证后续 copyfileobj 从头完整写入）
        校验失败抛 HTTPException(400)，路由层无需额外捕获即可返回明确错误。
        返回净化后的文件名，供 MinIO 对象 key 使用。
        """
        filename = file.filename or ""
        if not filename or Path(filename).name != filename:
            raise HTTPException(status_code=400, detail="非法文件名")
        suffix = Path(filename).suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            raise HTTPException(status_code=400, detail=f"不支持的文件类型: {suffix}，仅支持 {sorted(ALLOWED_SUFFIXES)}")
        file.file.seek(0, 2)
        size = file.file.tell()
        file.file.seek(0)  # 复位读指针，后续保存本地时从头写
        if size <= 0:
            raise HTTPException(status_code=400, detail="文件内容为空")
        if size > MAX_FILE_SIZE:
            raise HTTPException(status_code=400, detail=f"文件过大（{size} 字节），上限 {MAX_FILE_SIZE} 字节")
        return Path(filename).name

    def _generate_task_id(self):
        return uuid.uuid4().hex[:8]  # 生成8位随机数作为task_id


    def _get_date_path(self, local_base_dir):
        return os.path.join(local_base_dir, datetime.datetime.now().strftime("%Y%m%d"))


    def _upload_file_to_local(self, file, file_dir) -> Path:
        """将上传文件保存到本地"""
        # 1. 创建文件的归属目录
        os.makedirs(file_dir, exist_ok=True)

        try:
            import_file_path = os.path.join(file_dir, file.filename)
            with open(import_file_path, "wb") as f:
                shutil.copyfileobj(file.file, f)  # 带缓冲区写操作。效率高   1Mb
        except Exception as e:
            logger.error(f"文件保存本地出错: {e}")
            raise FileProcessingError(f"文件保存本地出错: {e}")

        return import_file_path


    def _upload_file_to_minio(self, import_file_path, file_name):
        """将上传文件保存到minio"""
        #1.获取minio客户端
        try:
            minio_client = StorageClients.get_minio_client()
        except ConnectionError as e:
            logger.warning(f"获取minio客户端出错: {e}")
            return   # 降级处理： 获取客户端失败，不影响后续流程。
        try:
            config = get_config()
            obj_name = f"origin_files/{datetime.datetime.now().strftime('%Y%m%d')}/{file_name}"
            # url = http://127.0.0.1:9000/knowledge-base-files/origin_files/20260913/万用表RS-12的使用.pdf
            minio_client.fput_object(config.minio_bucket, obj_name, import_file_path)
        except MinioError as e:
            logger.warning(f"文件上传Minio出错: {e}")
            return  # 降级处理： 上传Minio失败，不影响后续流程。
