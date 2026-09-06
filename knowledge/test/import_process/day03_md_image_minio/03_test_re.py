import re
from pathlib import Path

# 模拟MD全文：两处图片引用，路径是本地的 ./images/xxx.jpg
md_content = "这是一张图片 ![](./images/01ff135dc95789f7cb428c34df92a77869db4f4e70b83d663d1c485a17e416c1.jpg) 和另一张 ![](./images/10d2f007e02047a07d46e75a81db7f96811916c0f5ff662fa23ce215dadcbbe1.jpg)"

# 匹配MD图片语法 ![alt](url)：两个捕获组分别抓alt文本和路径
# (.*?) 非贪婪：遇到第一个 ] 或 ) 就停，防止一行多图时跨图吞并
pattern = re.compile(r"!\[(.*?)\]\((.*?)\)")

# 模拟VLM摘要结果：{图片文件名: 摘要文本}
summaries = {
    '01ff135dc95789f7cb428c34df92a77869db4f4e70b83d663d1c485a17e416c1.jpg': '万用表RS-12直流电流测量接线示意图（10A档位）',
    '10d2f007e02047a07d46e75a81db7f96811916c0f5ff662fa23ce215dadcbbe1.jpg': '蜂鸣器功能符号指示'
}
# 模拟上传MinIO后的地址映射：{图片文件名: 远程URL}
remote_urls = {
    '01ff135dc95789f7cb428c34df92a77869db4f4e70b83d663d1c485a17e416c1.jpg': 'http://192.168.10.151:9000/bucket/01ff135dc95789f7cb428c34df92a77869db4f4e70b83d663d1c485a17e416c1.jpg',
    '10d2f007e02047a07d46e75a81db7f96811916c0f5ff662fa23ce215dadcbbe1.jpg': 'http://192.168.10.151:9000/bucket/10d2f007e02047a07d46e75a81db7f96811916c0f5ff662fa23ce215dadcbbe1.jpg'
}


# 回调函数：pattern.sub 对每个匹配到的图片引用调用一次，返回值就是替换后的文本
def replacer(match: re.Match) -> str:
    original_path = match.group(2).strip()      # 取出 () 里的本地路径
    file_name_in_md = Path(original_path).name  # ./images/xxx.jpg -> xxx.jpg（MD里的路径可能带目录前缀，字典key只有文件名，需对齐）
    for img_name, summary in summaries.items():
        if img_name == file_name_in_md:
            # 命中：alt填摘要、路径换远程URL，一次完成两处改写
            return f"![{summary}]({remote_urls[img_name]})"
    return match.group(0)  # 没命中（如表格图不在summaries里）：原样返回，不动它

# group(0) — 表示整个匹配结果（即完整匹配到的字符串），不是任何一个捕获组。
# group(1) — 第 1 个捕获组 (.*?)，即 [] 中的内容（图片的 alt 文本）。
# group(2) — 第 2 个捕获组 (.*?)，即 () 中的内容（图片的 URL/路径）。

# sub：扫全文所有 ![...](...)，逐个交给 replacer 决定替换成什么
result = pattern.sub(replacer, md_content)
print(result)
