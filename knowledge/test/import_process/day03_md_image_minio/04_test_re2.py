import re
#text = "第一句话。    第二句话！      第三句话？       "
text = "第一句话。第二句话！第三句话？"
result = re.split(r'(?<=[。！？；.!?;])\s*', text)

#['第一句话。', '第二句话！', '第三句话？', '']
print(result)

# 正向后瞻断言