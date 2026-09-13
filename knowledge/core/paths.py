import os

# knowledge 包根目录  D:\PyProjects\shopkeeper_brain\knowledge（由 paths.py 所在的 core/ 上一级推出）
KNOWLEDGE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# 本地文件存储基础目录  D:\PyProjects\shopkeeper_brain\knowledge\temp_data（运行时自动创建）
LOCAL_BASE_DIR = os.path.join(KNOWLEDGE_ROOT, "temp_data")

# 前端页面静态资源目录  D:\PyProjects\shopkeeper_brain\knowledge\front
FRONT_PAGE_DIR = os.path.join(KNOWLEDGE_ROOT, "front")

def get_local_base_dir() -> str:
    """获取本地文件存储基础目录"""
    return LOCAL_BASE_DIR

def get_front_page_dir() -> str:
    """获取前端静态页面目录"""
    return FRONT_PAGE_DIR