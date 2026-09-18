import os

# knowledge 包根目录  D:\PyProjects\shopkeeper_brain\knowledge（由 paths.py 所在的 core/ 上一级推出）
KNOWLEDGE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# 本地文件存储基础目录  D:\PyProjects\shopkeeper_brain\knowledge\temp_data（运行时自动创建）
LOCAL_BASE_DIR = os.path.join(KNOWLEDGE_ROOT, "temp_data")

# Vue/Vite 前端构建产物目录  D:\PyProjects\shopkeeper_brain\knowledge\web\dist
WEB_DIST_DIR = os.path.join(KNOWLEDGE_ROOT, "web", "dist")

def get_local_base_dir() -> str:
    """获取本地文件存储基础目录"""
    return LOCAL_BASE_DIR

def get_web_dist_dir() -> str:
    """获取 Vue/Vite 前端构建产物目录"""
    return WEB_DIST_DIR
