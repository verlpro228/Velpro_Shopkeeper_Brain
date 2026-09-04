from minio import Minio

# 初始化客户端
client = Minio(
    "192.168.10.151:9000",
    access_key="minioadmin",
    secret_key="minioadmin",
    secure=False  # 是否使用https
)

if not client.bucket_exists("velpro"):
    client.make_bucket("velpro")

# 上传文件
client.fput_object(
    bucket_name="velpro",
    object_name="images/微信图片_20260706133030_122_40.jpg",  # Object名称含路径
    file_path="D:/wallpaper/微信图片_20260706133030_122_40.jpg",  # 上传的本地文件路径
    content_type="image/jpeg"  # MIME类型
)

# 上传后访问地址
url = f"http://192.168.10.151:9000/velpro/images/微信图片_20260706133030_122_40.jpg"
print(url) # 桶的权限要开放  private -> public