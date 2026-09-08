"""
处理Markdown图片节点模块

流程：读取MD -> 扫描图片上下文 -> (VLM生成图片摘要) -> (上传MinIO替换路径)
"""
import threading
from collections import deque
import json
import logging
import os
from dataclasses import dataclass
from multiprocessing import context
import time
from typing import Deque, Dict, List, Tuple, Optional
from pathlib import Path
import re

import base64
from langchain_openai import OpenAI

from knowledge.processor.import_process.exceptions import FileProcessingError, StateFieldError, ValidationError
from knowledge.processor.import_process.state import ImportGraphState
from knowledge.processor.import_process.base import BaseNode, setup_logging
from logging import Logger

from knowledge.utils.client.ai_clients import AIClients
from knowledge.utils.client.storage_clients import StorageClients


# ==================== 数据模型 ====================
# ImageContext：一张图片在 MD 正文中的"上下文"（给VLM看的辅助信息）
@dataclass
class ImageContext:
  heading:str     # 图片上方最近的章节标题
  pre_text:str    # 图片前的正文（限长截取）
  post_text:str   # 图片后的正文（限长截取）


# ImageInfo：一张图片的完整信息（文件名 + 路径 + 上下文）
@dataclass
class ImageInfo:
  name:str    #图片文件名，如 "图1.png"
  path:str    #图片本地完整路径
  context:ImageContext  # 在md文档中的上下文


# 读取和备份Markdown文件的类
class MdFileHandler:
  def __init__(self,logger:Logger,node_name:str):
    self.logger = logger
    self.node_name = node_name

  def read_md(self,state: ImportGraphState) -> Tuple[str,Path,Path]:
    """从状态中读MD文件：校验路径 -> 读全文 -> 推导图片目录"""
    self.logger.info("读取MD文件内容，返回MD内容，MD路径，图片目录路径")
    md_path = state.get("md_path","")
    if not md_path:
      # 状态里缺少 md_path 字段，抛状态字段异常
      raise StateFieldError(
        node_name=self.node_name,
        field_name="md_path",
        expected_type=str
      )
    md_path_obj = Path(md_path)      # 字符串转 Path 对象，方便路径操作
    if not md_path_obj.exists():
      raise FileProcessingError("md_path文件不存在",self.node_name)

    with open(md_path_obj,"r",encoding="utf-8") as f:
      md_content = f.read()          # 一次性读入MD全文

    # 约定：图片放在 md 同级的 images/ 目录下
    images_dir = md_path_obj.parent / "images"
    return md_content,md_path_obj,images_dir

  def backup(self,md_path_obj:Path,new_md_content:str):
    """
      md_path_obj:旧MD文件路径
      new_md_content:新MD文件内容
    """
    # 备份替换Image摘要和地址后的新md文件
    self.logger.info("备份新MD文档!!!")
    # 万用表的使用.md -> 万用表的使用_new.md
    backup_path = md_path_obj.with_name(md_path_obj.stem + "_new.md")
    with open(backup_path,"w",encoding="utf-8") as f:
      f.write(new_md_content)



# 图片上下文扫描器
class ImageScanner:
  """遍历图片目录，为每张图片在MD正文中定位上下文"""

  def __init__(self,logger:Logger,node_name:str):
    self.logger = logger
    self.node_name = node_name

  def scan_img_dir(self,md_content:str,images_dir:Path,image_extensions:set[str],context_length:int=200) -> List[ImageInfo]:
    """主流程：目录里每个合法图片 -> 找上下文 -> 收集为 ImageInfo 列表"""

    image_info_list:List[ImageInfo] = []  # 结果收集器

    for image_path in images_dir.iterdir():  # 逐个遍历目录条目

      if not image_path.is_file():
        # 不是文件 直接跳过
        continue

      if not image_path.suffix in image_extensions: #不是合法后缀名直接跳过
        # raise ValidationError(f"图片:{image_path}后缀格式错误：{image_path.suffix}",self.node_name)
        continue


      # 查找图片的上下文
      # 查找图片上下文 如果表格图片 在md_content中可能找不到 返回None上下文ImageContext对象
      ctx = self._find_context(md_content,image_path.name,context_length)
      if ctx is None:
        # 正文中没引用该图（如表格图），跳过
        continue

      image_info_list.append(ImageInfo(  # 打包：文件名+路径+上下文
        name=image_path.name,
        path=image_path,
        context=ctx
      ))
    return image_info_list



  # def _find_context(self,image_path,md_content,context_length) -> Optional[ImageContext]:
  #   pass

  def _find_context(self, md_content: str, img_name: str, max_chars: int = 200) -> ImageContext | None:
    """返回图片在 MD 中第一次出现位置的上下文，找不到返回 None。"""
    # 正则匹配 MD 图片语法 ![任意文字](任意路径/图片名...)；re.escape 防文件名特殊字符破坏正则
    pattern = re.compile(
      r"!\[.*?\]\(.*?" + re.escape(img_name) + r".*?\)"
    )
    md_lines = md_content.split("\n")  # 按行拆分，便于行级定位

    # 遍历时同时拿到「序号 + 元素」。
    for line_idx, line in enumerate(md_lines):
      if not pattern.search(line):  # 本行没有该图片引用，看下一行
        continue

      # 向上：找最近标题，取标题到图片之间的内容作为上文
      prev_title, prev_boundary = self._find_heading_above(md_lines, line_idx)
      pre_content = md_lines[prev_boundary + 1: line_idx]  # 标题(不含) ~ 图片行(不含)
      img_pre = self._extract_limited_context(pre_content, max_chars, direction="front")

      # 向下：找下一个标题，取图片到标题之间的内容作为下文
      next_boundary = self._find_heading_below(md_lines, line_idx)
      post_content = md_lines[line_idx + 1: next_boundary]  # 图片行(不含) ~ 下个标题(不含)
      img_post = self._extract_limited_context(post_content, max_chars, direction="end")

      return ImageContext(heading=prev_title,pre_text=img_pre,post_text=img_post,)  # 找到即返回第一次出现处
    return None  # 全文没引用该图片

  @staticmethod
  def _find_heading_above(md_lines: List[str], from_idx: int) -> Tuple[str, int]:
    """从 from_idx 向上查找最近的标题。"""
    for i in range(from_idx - 1, -1, -1):  # 从当前行的上一行一直扫到第0行
      if re.match(r"^#{1,6}\s+", md_lines[i]):  # 1~6个#开头 = Markdown标题
        return md_lines[i], i  # 返回标题文本和行号
    return "", -1  # 没找到：空标题，边界为-1（上文从第0行开始）

  @staticmethod
  def _find_heading_below(md_lines: List[str], from_idx: int) -> int:
    """从 from_idx 向下查找下一个标题。"""
    for i in range(from_idx + 1, len(md_lines)):  # 从当前行的下一行扫到文末
      if re.match(r"^#{1,6}\s+", md_lines[i]):  # 找到下一个标题
        return i  # 返回其行号，作为下文结束边界
    return len(md_lines)  # 没找到：边界为文末（下文取到最后一行）

  @staticmethod
  def _extract_limited_context(lines: List[str], max_chars: int, direction: str) -> str:
    """按段落分割，按 direction 方向贪心装填，保持段落完整性。"""
    # 第一步：把行列表切成段落（空行/其他图片行 = 段落分隔符）
    current_paragraph: List[str] = []  # 正在累积的当前段
    paragraphs: List[str] = []         # 切好的段落列表

    for line in lines:
      #line.strip(): 去除字符串首尾的空白字符(空格、制表符、换行符等)
      is_blank_line = not line.strip()  # 空行判断
      is_other_image = re.match(        # 其他图片引用行判断（也视为段落边界）
        r"^!\[.*?\]\(.*?\)$", line.strip()
      )

      if is_blank_line or is_other_image:
        if current_paragraph:  # 段落结束：收进列表并重置
          paragraphs.append("\n".join(current_paragraph))
          current_paragraph = []
        continue

      # 普通的文字行 直接添加进当前段
      current_paragraph.append(line)  # 普通行加入当前段

    if current_paragraph:  # 收尾：最后一段可能没有空行结束
      paragraphs.append("\n".join(current_paragraph))

    # 第二步：贪心装填，控制在 max_chars 内且不截断段落
    if direction == "front":
      paragraphs.reverse()#就近原则  # 上文反转：最靠近图片的段排最前

    total = 0
    selected: List[str] = []
    for para in paragraphs:
      if (total + len(para) > max_chars) and selected:#至少有个段落  # 超长且已有内容就停
        break
      selected.append(para)
      total += len(para)

    if direction == "front":
      selected.reverse()#与原文顺序一致，利于VLM  # 上文还原回原文顺序

    return "\n\n".join(selected)#折行并空一行



# 视觉模型摘要器
class VLMSummarizer:
  """调用视觉大模型，结合上下文为每张图片生成文字摘要"""

  def __init__(self,logger:Logger,node_name:str):
    self.logger = logger
    self.node_name = node_name
    self.stampts_deque:Deque[float] = deque()
    self.lock = threading.Lock()

  def summarizer_all(self,document_name:str,imageinfo_list:List[ImageInfo],vl_model:str,requests_per_minute:int = 5) -> Dict[str, str]:
    # 批量入口：逐张调VLM生成摘要，返回 {图片文件名: 摘要文本}，供后续替换MD和入库使用
    summaries:Dict[str,str] = {}  # 结果字典：图片名 -> 摘要
    # stampts_deque:Deque[float] = deque() 不能每次请求都创建一个 应该多个请求共用一个队列

    try:
      # 从全局客户端管理器拿OpenAI单例（.env里的key/base_url已在内部处理）
      openai_client = AIClients.get_openai()
    except Exception as e:
      # 降级策略：拿不到客户端（如缺DASHSCOPE_API_KEY）不中断整条流水线，
      # 所有图片统一填占位摘要后提前返回，文档照常入库
      self.logger.error(f"获取OpenAi客户端失败：{e}")
      for image_info in imageinfo_list:
        summaries[image_info.name] = "默认图片摘要"
      return summaries
    
    for image_info in imageinfo_list:  # 串行遍历每张图片
      # 限流 requests_per_minute：控制调用节奏（如每次sleep 60/rpm秒），避免触发DashScope的QPS限制
      # stampts_deque 存放每次请求的时间戳，用于计算间隔时间
      self._enforce_rate_limit(requests_per_minute)
      
      # 调用VLM获取摘要
      summary = self._summarize_one(image_info,openai_client,vl_model,document_name)  # 单图处理：读图+拼上下文提示词+调模型
      summaries[image_info.name] = summary  # 以文件名为key收集结果
    return summaries


  def _summarize_one(self,image_info:ImageInfo,openai_client:OpenAI,vl_model:str,document_name:str) -> str:
    """调用VLM获取图片摘要"""
    parts = [p for p in (image_info.context.heading,image_info.context.pre_text,image_info.context.post_text) if p]
    final_context = "\n".join(parts)

    # 图片转 Base64
    with open(image_info.path, "rb") as f:
      base64_image = base64.b64encode(f.read()).decode("utf-8")

    # 调用 VLM
    response = openai_client.chat.completions.create(
      model=vl_model,  # 视觉模型
      messages=[
        {
          "role": "user",
          "content": [
            {
              "type": "text",
              "text": (
                f"任务：为Markdown文档中的图片生成一个简短的中文标题。\n"
                f"背景信息：\n"
                f"  1. 所属文档标题：\"{document_name}\"\n"
                f"  2. 图片上下文：{final_context}\n"
                f"请结合图片内容和上述上下文信息，"
                f"用中文简要总结这张图片的内容，"
                f"生成一个精准的中文标题（不要包含图片二字）。"
              )
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
    return summary

  def _enforce_rate_limit(self,requests_per_minute,window:int=60):
    with self.lock:
      # 滑动窗口限流：deque里存最近window秒内每次请求的时间戳，保证任意60秒内请求数不超requests_per_minute
      now = time.time()  # 当前时刻
      while self.stampts_deque and now - self.stampts_deque[0] > window:
        self.stampts_deque.popleft()  # 队首已超出窗口（太旧），弹出丢弃，deque里只留窗口内的请求
      if len(self.stampts_deque) >= requests_per_minute:
        # 窗口内已打满配额，必须等待：最旧的请求"出窗"后才能再发
        sleep_duration = window - (now - self.stampts_deque[0])  # 还要等多久，队首那个请求才满window而失效
        if sleep_duration > 0:
          time.sleep(sleep_duration)  # 阻塞等待到下一个请求滑出窗口
        now = time.time()  # 睡醒后时间已变，重新取当前时刻
        while self.stampts_deque and now - self.stampts_deque[0] > window:
          self.stampts_deque.popleft()  # 再清一遍：等待期间过期的时间戳现在该出窗了
      self.stampts_deque.append(now)  # 记录本次请求时刻，占掉一个配额  



# 图片存储和替换
class ImageUploader:
  """把本地图片上传到MinIO，并替换MD中的引用路径为远程URL"""

  def __init__(self,logger:Logger,node_name:str):
    self.logger = logger
    self.node_name = node_name

  # 上传图片到minio和替换md中的图片摘要和路径
  def upload_and_replace(self, document_name:str, md_content:str, imageinfo_list:List[ImageInfo], summaries:Dict[str,str], minio_bucket:str,minio_endpoint:str):
    # 两步编排：先全部上传拿到URL映射，再统一改写MD。
    # 拆开是因为上传有网络失败风险，先确定"每张图最终用哪个地址"，替换阶段才不会写坏文档
    # 图片名称 对应 远程图片URL 字典（上传失败的图退化为本地路径）
    remote_urls:Dict[str,str] = self._upload_all(imageinfo_list,document_name,minio_bucket,minio_endpoint)

    # 替换图片路径和摘要的md文件内容
    new_md_content = self._replace_in_md(
      remote_urls=remote_urls,
      md_content=md_content,
      imageinfo_list=imageinfo_list,
      summaries=summaries
    )
    return new_md_content


  # 上传所有图片到minio
  # 返回图片名称 对应 远程图片URL 字典
  def _upload_all(self, imageinfo_list:List[ImageInfo], document_name:str, minio_bucket:str, minio_endpoint:str)->Dict[str,str]:
    remote_urls:Dict[str,str] = {}
    # 获取minio服务器连接
    try:
      minio_client = StorageClients.get_minio_client()
    except Exception as e:
      # 主降级处理 当连接获取不到 所有图片都降级处理
      # 连不上MinIO（服务挂了/密钥没配）：一张都传不了，全部保留本地路径，导入流程继续走
      for image_info in imageinfo_list:
        remote_urls[image_info.name] = image_info.path
      return remote_urls

    # 循环上传图片
    for image_info in imageinfo_list:
      try:
        # fput_object：把本地文件流式上传到指定桶的指定对象名（file_path 接受 str 或 Path）
        minio_client.fput_object(
          bucket_name=minio_bucket, # 存储桶名称
          object_name=f"{document_name}/{image_info.name}", #上传后图片名称（带目录名称）
          file_path=image_info.path,  # 本地图片路径
        )
        # 拼出浏览器可访问的公网地址：http://IP:9000/桶名/文档名/图片名
        remote_url = f"{minio_endpoint}/{minio_bucket}/{document_name}/{image_info.name}"
        remote_urls[image_info.name] = remote_url
      except Exception as e:
        # 二级降级处理 只针对某个上传失败的图片进行降级处理
        # 单张失败（文件被占用、权限不足等）不牵连其他图，这张退回本地路径
        remote_urls[image_info.name] = image_info.path

    return remote_urls



  # 改写MD：把 ![](本地路径) 换成 ![](MinIO地址)，并把VLM摘要填进图片的alt文本 ![摘要](...)
  # 摘要写进alt文本后，切片入库时图片就变成"有文字语义"的内容，纯图也能被检索命中
  def _replace_in_md(self, remote_urls:Dict[str,str], md_content:str, imageinfo_list:List[ImageInfo], summaries:Dict[str,str]):
    pattern = re.compile(r"!\[(.*?)\]\((.*?)\)")
    def replacer(match):
      original_url = match.group(2).strip() #匹配到的图片的本地地址
      image_url_md = Path(original_url).name  #图片在md中的文件名称 带扩展名
      for image_name,summary in summaries.items():
        if image_name == image_url_md:
          return f"![{summary}({remote_urls[image_name]})"  # 匹配内容被替换后结果
      return match.group(0)  # 正则表达式匹配到的原文
    # 替换MD中的图片引用
    return pattern.sub(replacer, md_content)


# 处理Markdown图片节点
class MdImgNode(BaseNode):
  """流水线节点：MD中图片的上下文提取 -> 摘要 -> 上传替换，整条链路的编排者"""
  name = "md_img_node"  # 节点名，覆盖基类默认值

  def __init__(self):
    super().__init__()  # 初始化基类：拿到 self.config 和 self.logger
    # 组合四个功能模块，各自职责单一
    self.md_file_handler = MdFileHandler(self.logger,self.name)    # 读MD文件
    self.image_scanner = ImageScanner(self.logger,self.name)       # 扫描图片上下文
    self.vlm_summarizer = VLMSummarizer(self.logger,self.name)      # VLM图片摘要
    self.image_uploader = ImageUploader(self.logger,self.name)     # 上传+替换路径

  def process(self, state: ImportGraphState) -> ImportGraphState:
    # 文件处理 获取文件内容 获取整个md文档 获取文件路径
    # md_path = state.get("md_path")
    md_content,md_path_obj,images_dir = self.md_file_handler.read_md(state)
    if not images_dir.exists():
      # 没有图片目录 = MD里没有图片，直接把原文存入状态返回
      state["md_content"] = md_content
      return state
    
    # 获取图片上下文
    imageinfo_list:List[ImageInfo] = self.image_scanner.scan_img_dir(
      md_content,
      images_dir, 
      image_extensions=self.config.image_extensions, # 图片扩展名 {"jpg","png","jpeg","gif"}
      context_length=self.config.img_content_length  # 图片上下文长度
    )
    
    # 调用视觉模型 生成图片摘要
    summaries:Dict[str,str] = self.vlm_summarizer.summarizer_all(
      document_name=md_path_obj.stem,   # 文档标题：取文件名去扩展名（"万用表.pdf" -> "万用表"），给VLM当全局语境
      imageinfo_list=imageinfo_list,             # 上一步扫出的图片信息列表（含每张图的路径与上下文）
      vl_model=self.config.vl_model,          # 视觉模型名，来自 .env 的 VL_MODEL
      requests_per_minute=self.config.requests_per_minute,  # VLM 调用限速（默认10次/分），防止触发平台QPS限制
    )
    # 产出 {图片文件名: 摘要文本}，内部已对"客户端不可用"降级（全填占位摘要），这里拿到的一定是完整字典
    print(summaries)  # 临时调试输出，联调完删掉

    # 上传图片到minio 替换md中图片的路径
    # 替换图片路径和摘要的md文件内容
    new_md_content:str = self.image_uploader.upload_and_replace(
      document_name = md_path_obj.stem,   # 作为MinIO对象名的目录前缀（万用表/图1.png），隔离不同文档的同名图
      md_content = md_content,            # 原始MD全文，替换的基准文本
      imageinfo_list = imageinfo_list,    # 需要上传的图片清单
      summaries = summaries,               # 上一步的摘要，写进图片alt文本 ![摘要](URL)
      minio_bucket = self.config.minio_bucket,      # 存储桶名，来自 .env 的 MINIO_BUCKET
      minio_endpoint = self.config.get_minio_base_url()   # 拼接远程URL用的服务地址（http://IP:9000）
    )

    # 备份替换后的md文档
    self.md_file_handler.backup(md_path_obj,new_md_content)

    return state

if __name__ == "__main__":
  setup_logging()
  node = MdImgNode()
  state = {
    "md_path":r"C:\Users\14207\Desktop\doc\temp_dir\万用表RS-12的使用\auto\万用表RS-12的使用.md"
  }
  processed_state = node(state)
  print(json.dumps(processed_state,indent=4,ensure_ascii=False))
