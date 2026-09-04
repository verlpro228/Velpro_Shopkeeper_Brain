import os
import subprocess
from pathlib import Path

# 基础用法：run() - 同步执行，等待完成
os.environ['MINERU_MODEL_SOURCE'] = 'modelscope'#设置MinerU的模型来源 ModelScope（阿里魔搭）
os.environ['MODELSCOPE_OFFLINE'] = '1'#1 表示开启离线模式（MODELSCOPE_CACHE 指定的目录）
os.environ['HF_HUB_OFFLINE'] = '1'#启用 HuggingFace Hub 离线模式
os.environ['TRANSFORMERS_OFFLINE'] = '1'#启用 Transformers 库离线模式

print("✅ 环境变量已配置")
print(f"   MINERU_MODEL_SOURCE = {os.environ.get('MINERU_MODEL_SOURCE')}")
print(f"   MODELSCOPE_OFFLINE = {os.environ.get('MODELSCOPE_OFFLINE')}")
print(f"   HF_HOME = {os.environ.get('HF_HOME')}")
print()

# mineru -p E:\doc\万用表RS-12的使用.pdf -o E:\temp_dir --source=local --backend pipeline
# mineru -p E:\doc\万用表RS-12的使用.pdf -o E:\temp_dir --source=local -b pipeline
result = subprocess.run(
    [
        "mineru",
        "-p",
        r"C:\Users\14207\Desktop\doc\hak180产品安全手册.pdf",
        "-o",
        r"C:\Users\14207\Desktop\doc\temp_dir",
        "--backend",
        "pipeline",
        "--source",
        "local"
    ],
    capture_output=True,  # 捕获子进程的标准输出和标准错误，结果可通过 .stdout / .stderr 获取
    encoding="utf-8",  # 以 UTF-8 编码解码输出，返回字符串而非字节
    #errors="strict"（默认）→ 直接报错 UnicodeDecodeError
    errors="replace",  # 遇到无法解码的字符用  替换       errors="ignore"直接丢弃无效字节
    check=True,  # 子进程返回非零退出码时**自动抛出** `CalledProcessError` 异常
)

print(result.stdout)