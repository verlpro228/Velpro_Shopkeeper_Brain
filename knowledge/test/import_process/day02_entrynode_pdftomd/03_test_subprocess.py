import os
import subprocess

# 高级用法：Popen() - 实时获取输出
# ===============================================================

# === 关键：加载环境变量（模拟 PyCharm 运行 pdf_to_md.py 时的环境）===
# 方法1：直接读取 .env 文件并设置环境变量
# env_file = Path(__file__).parent.parent.parent / '.env'
# if env_file.exists():
#     with open(env_file, 'r', encoding='utf-8') as f:
#         for line in f:
#             line = line.strip()
#             if line and not line.startswith('#') and '=' in line:
#                 key, value = line.split('=', 1)
#                 os.environ[key.strip()] = value.strip()

# 方法2：显式设置关键环境变量（确保 MinerU 使用 ModelScope 离线模式）
os.environ['MINERU_MODEL_SOURCE'] = 'modelscope'
os.environ['MODELSCOPE_OFFLINE'] = '1'
os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'

print("✅ 环境变量已配置")
print(f"   MINERU_MODEL_SOURCE = {os.environ.get('MINERU_MODEL_SOURCE')}")
print(f"   MODELSCOPE_OFFLINE = {os.environ.get('MODELSCOPE_OFFLINE')}")
print(f"   HF_HOME = {os.environ.get('HF_HOME')}")
print()

# mineru -p E:\doc\万用表RS-12的使用.pdf -o E:\temp_dir --source=local --backend pipeline
# mineru -p E:\doc\万用表RS-12的使用.pdf -o E:\temp_dir --source=local -b pipeline
proc = subprocess.Popen(
    args=["mineru", "-p", r"C:\Users\14207\Desktop\doc\hak180产品安全手册.pdf", "-o", r"C:\Users\14207\Desktop\doc\temp_dir", "--source", "local", "--backend", "pipeline"],
    stdout=subprocess.PIPE,    # 捕获标准输出
    stderr=subprocess.STDOUT,  # 合并错误到标准输出
    text=True,
    encoding="utf-8",
    errors="replace",          # 遇到乱码时替换
    bufsize=1                  # 行缓冲，实时输出
)

for line in proc.stdout:
    print(line.rstrip())

return_code = proc.wait()
print(return_code) # 0 成功    非零  失败