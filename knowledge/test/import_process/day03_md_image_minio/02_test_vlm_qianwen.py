import os

from openai import OpenAI
import base64

# 初始化客户端
client = OpenAI(
    api_key=os.getenv("DASHSCOPE_API_KEY"),      #  老师已经配置在系统环境变量中了。
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1" #对请求 QPS 有限制： 我们不超过10次
)

# 图片转 Base64
with open("D:/wallpaper/微信图片_20260706133030_122_40.jpg", "rb") as f:
    base64_image = base64.b64encode(f.read()).decode("utf-8")

print(base64_image)


# 调用 VLM
response = client.chat.completions.create(
    model="kimi-k2.7-code",  # 视觉模型
    #model="qwen3-vl-plus", #视觉模型
    messages=[
        {
            "role": "user",
            "content": [
                {
                    "type": "text",        # 告诉 API：这是一段文字
                    "text": "请描述这张图片的内容"
                },
                {
                    "type": "image_url",   # 告诉 API：这是一张图片
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{base64_image}"
                    }
                }
            ]
        }
    ],
    max_tokens=100
)

summary = response.choices[0].message.content
print(summary)