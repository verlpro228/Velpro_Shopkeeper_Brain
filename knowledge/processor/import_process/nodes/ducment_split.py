import os
import re
from typing import Any, Dict, List, Tuple

import json
from langchain_text_splitters import RecursiveCharacterTextSplitter

from knowledge.processor.import_process.base import BaseNode, setup_logging
from knowledge.processor.import_process.exceptions import StateFieldError, ValidationError
from knowledge.processor.import_process.state import ImportGraphState
from knowledge.utils.markdown_util import MarkdownTableLinearizer


# 文档的切分节点
class DocumentSplitNode(BaseNode):
  name:str = "document_split_node"

  def process(self, state: ImportGraphState) -> ImportGraphState | dict:
    # 文档切分处理
    # 1. 获取输入参数并且校验 md_content,file_title,max_content_length,min_content_length
    md_content, file_title, max_content_length, min_content_length = self._get_input_validation(state)

    # 按照标题切分：按\n获取文档行的集合，遍历，判断是否存在代码围挡 正则表达式识别标题 构建section
    # 2. 根据标题切割(核心)
    sections:List[Dict[str,Any]] = self._split_by_title(md_content, file_title)

    # 3. 处理(切分和合并)
    final_sections:List[Dict[str,Any]] = self.split_and_merge(sections, max_content_length, min_content_length)

    # 4. 组装
    chunks = self._assemble_chunk(final_sections)

    # 5. 更新state:chunks
    state['chunks'] = chunks

    # 6. 日志统计
    self._log_summary(md_content, chunks, max_content_length)

    # 7. 备份
    self._backup_chunks(state, chunks)


    # 8. 返回
    return state

  def _get_input_validation(self,state:ImportGraphState)->Tuple[str,str,int,int]:
    # 校验输入参数是否存在
    md_content = state.get('md_content')  # 整个文档内容
    file_title = state.get('file_title')  # 文件标题 不带扩展名
    max_content_length = self.config.max_content_length  # 最大切片阈值 大于这个阈值需要二次切分
    min_content_length = self.config.min_content_length  # 最小切片阈值 小于这个阈值需要合并切分
    
    if not md_content:
      raise StateFieldError(f"切分的文档内容不存在！",self.name)
    if not file_title:
      raise StateFieldError(f"切分的文件名称不存在！",self.name)
    if max_content_length < 0:
      raise ValidationError(f"参数错误：最大切分阈值max_content_length不能小于0！",self.name) 
    if min_content_length < 0:
      raise ValidationError(f"参数错误：最小切分阈值min_content_length不能小于0！",self.name)
    if max_content_length <= min_content_length:
      raise ValidationError(f"参数错误：最大切分阈值max_content_length不能小于等于最小切分阈值min_content_length！",self.name)
    
    return md_content, file_title, max_content_length, min_content_length


  # 按标题层级切文档：标题=分隔符，每遇到新标题就把"上一标题+其正文"打包成一个section
  def _split_by_title(self,md_content,file_title)->List[Dict[str,Any]]:
    sections:List[Dict[str,Any]] = []   # 成品：section字典的列表
    in_fence = False  #是否在围栏内（围栏内的 # 是shell注释，不能当标题）
    heading_re = re.compile(r"^\s*(#{1,6})\s+.+")  #匹配标题正则表达式；括号捕获#串，便于数长度得等级
    body:List[str] = []   #收集正文内容（当前section的"篮子"）
    content_lines = md_content.split("\n")  # 逐行处理，行是最小判断单位
    current_title = ""  # 当前标题
    current_level = 0  # 当前标题等级
    hierarchy = [""]*7  # 标题层级结构  ["","一级","二级","三级","四级","五级","六级"]
    # 下标0留空，让 hierarchy[3] 直接对应三级标题，免去 level-1 换算
    # parent_title = ""  # 父标题


    # 把当前标题前的内容封装成section -> List（闭包：直接读写外层 title/body/hierarchy 等变量）
    def _flush():
      if current_title or body:  # 空档期（文档开头还没出现标题）不打包，避免产出空section
        # 找爹：从"上一级"开始往上翻书签，第一个非空的就是最近的祖先标题
        parent_title = ""
        for lev in range(current_level-1,0,-1):  # 倒序：如三级标题查 hierarchy[2]→hierarchy[1]
          if hierarchy[lev]:
            parent_title = hierarchy[lev]
            break

        # 找不到爹的兜底：一级标题的爹=自己；正文在首个标题前（前言）的爹=文件名
        if not parent_title:
          parent_title = current_title if current_title else file_title

        sections.append({
          "parent_title": parent_title,
          "title": current_title if current_title else file_title,  # 前言没标题时，标题兜底用文件名
          "body": "\n".join(body),   # 篮子倒空：行列表拼回字符串
          "file_title": file_title
        })


    # 主循环：逐行扫描。标题行=换段信号，普通行=攒进当前篮子
    for index,line in enumerate(content_lines):
      if line.startswith("~~~") or line.startswith("```"):
        in_fence = not in_fence  # 遇到围栏开/关线，翻转状态（成对出现）
        
      # 匹配标题（围栏内强制视为普通行）
      match = heading_re.match(line) if not in_fence else None
      
      # 是标题
      if match:
        _flush()  #顺序关键：先把"上一个section"打包，再换标签（标题是分隔符，看到新标题才说明上一段结束）
        level = len(match.group(1))  # 标题等级 1-6：捕获组抓到"###"，数长度=3
        current_level = level
        current_title = line
        hierarchy[current_level] = current_title  # 本层书签换成新标题

        for i in range(level+1,7): #只清理大于当前级别的子级别 这些子级别是上个段落遗留的
          # 新标题出现=它的子孙章节全部作废；不清会在后续找爹时匹配到旧章节
          hierarchy[i] = ""

        body = []  # 换空篮子，开始攒新section的正文

      else:
        body.append(line.strip())  # 收集正文
    #处理最后一个段落 
    _flush() # 文档最后一个section后面没有"新标题"来触发打包，必须手动补一刀
    return sections  # 产出 [{parent_title,title,body,file_title},...]，供 split_and_merge 二次切分/合并


  # 二次切分和合并
  def split_and_merge(self, sections: List[Dict[str, Any]],
    max_content_length: int, min_content_length: int):
    """
    二次切分和合并
    Args:
      sections: 根据一级标题切分后的所有 section（章节）块
      max_content_length: 每一个 section 的 content 内容最大长度
      min_content_length: 触发合并的最小长度
    """
    self.log_step("step3", "切分及合并...")

    # 1. 切分
    current_sections = []
    for section in sections:
      current_sections.extend(self.split_long_section(section, max_content_length))

    # 2. 合并
    final_sections = self.merge_short_section(current_sections, min_content_length)

    # 3. 返回
    return final_sections


  # 对超长章节进行二次切分
  def split_long_section(self, section: Dict[str, Any], max_content_length: int=1000):
    """
    对超长章节进行二次切分
    """
    self.log_step("step3", "进行长内容的切分")

    # 1. 获取 section 对象属性
    title = section.get('title')
    body = section.get('body')
    file_title = section.get('file_title')
    parent_title = section.get('parent_title')

    # 2. 判断表格
    if "<table>" in body:
      self.logger.info("检测到了表格数据...")
      body = MarkdownTableLinearizer.process(body)
      section['body'] = body

    # 3. 对标题做校验
    MAX_TITLE_LENGTH = 50
    if len(title) > MAX_TITLE_LENGTH:
      self.logger.warning(f"检测文件{file_title}对应的{title}长度过长...")
      title = title[:MAX_TITLE_LENGTH]

    # 4. 拼接 title 前缀
    title_prefix = f"{title}\n\n"

    # 5. 计算总长度
    total_length = len(title_prefix) + len(body)
    # 6. 判断是否需要切分 若不超过最大长度，直接返回当前章节
    if total_length <= max_content_length:            
      return [section]

    # 7. 计算 body 可用的长度
    body_length = max_content_length - len(title_prefix)
    if body_length <= 0:
      return [section]

    # 8. 使用 RecursiveCharacterTextSplitter 切分
    if total_length > max_content_length:
      text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=body_length,  #切片的最大长度
        chunk_overlap=0,    #不重叠
        separators=["\n\n", "\n", "。", "！", "？", "；", ".", "!", "?", ";", " ", ""], #用于切割的分隔符
        keep_separator=False   #不保留分隔符
      )
      texts = text_splitter.split_text(body)

      # 8.3 判断切分结果
      if len(texts) <= 1:
          return [section]

      sub_sections = []
      for index, text in enumerate(texts):
        sub_sections.append({
          "title": section.get('title') + f"-{index + 1}",
          "body": text,
          "file_title": file_title,
          "parent_title": parent_title,
          "part": f"{index + 1}"  # 标记序号
        })

    return sub_sections

  # 合并过短的章节
  def merge_short_section(self, current_sections: List[Dict[str, Any]],min_content_length: int)->List[Dict[str, Any]]:
    """
    贪心累加算法合并过短的章节
    局限性：
    1. 撑破最小的阈值：大一点不用管
    2. 孤儿小块：也不用管（大量都是小块）
    """
    # 1. 定义变量
    # 双指针模型：current_section 是"正在装的箱子"，遍历时不断吸收相邻小节；
    # final_sections 是"已封箱的箱子堆"，装满了/换爹了的箱子会被放进去
    current_section = current_sections[0]
    final_sections:List[Dict[str, Any]] = []  # 最终的箱子

    # 2. 遍历以及合并（current 是被合并方，next 是待吸收方）
    for next_section in current_sections[1:]:
      # 同源检查：只有挂在同一个父标题下的小节才允许合并，避免跨章节内容"串味"
      same_parent = (current_section['parent_title'] == next_section['parent_title'])

      # 合并条件：同源 且 当前箱子还没装满（body 长度低于最小阈值）
      # 注意：只判断 current 的长度、不判断 next 的长度，
      # 所以合并结果可能超过 min_content_length（即 docstring 说的局限性1"撑破阈值"）
      if same_parent and len(current_section.get('body')) < min_content_length:
        # body的合并：去首尾空白后用空行拼接，保持 Markdown 段落分隔
        current_section['body'] = (
            current_section.get('body').rstrip() + "\n\n" + next_section.get('body').lstrip()
        )
        # 更新 current_title：合并后横跨多个小节，原标题已不准确，统一"降级"为父标题
        # part=0 是"本块被合并过"的标记，稍后第4步会重新编号
        current_section['title'] = current_section['parent_title']
        current_section['part'] = 0
      else:
        # 装不下了（或换爹了）：把 current_section 封箱，next_section 成为新的工作箱
        final_sections.append(current_section)
        # 更新 next_section
        current_section = next_section

    # 循环结束时 current_section 还悬在外面（没有下一个触发封箱），补一刀封箱
    final_sections.append(current_section)

    # 4. 只对合并块（part=0）重新编号；切分块在 split_long_section 已拼过 "-N" 后缀，再加会产出 "A-1- 1" 双重编号
    part_counter = {}  # 计数器：{父标题:。 已编号个数}
    result = []  # 最终结果
    for final_section in final_sections:
      if "part" in final_section:  # 只处理切分/合并过的块；普通章节无 part 字段，原样跳过
        parent_title = final_section.get('parent_title')  # 以父标题为分组 key
        part_counter[parent_title] = part_counter.get(parent_title, 0) + 1  # 每个父标题独立计数 1、2、3...
        new_part = part_counter[parent_title]  # 本块的新序号
        final_section['part'] = new_part  # 写回 part
        final_section['title'] = final_section['title'] + f"- {new_part}"  # 标题追加序号，保证 chunk 标题唯一

      result.append(final_section)  # 封箱

    return result


  # 组装 chunks：把内部 section 格式转换成入库用的 chunk 格式（body 并入 content，丢掉多余字段）
  def _assemble_chunk(self, final_chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """最终组合 chunk"""
    self.log_step("step4", "组装最终的切片信息...")
    chunks = []  # 成品列表

    for chunk in final_chunks:
      # 1. 获取 chunk 的信息
      title = chunk.get('title')
      file_title = chunk.get('file_title')
      parent_title = chunk.get('parent_title')
      body = chunk.get('body')
      content = f"{title}\n\n{body}"  # 标题+正文拼成向量化内容：检索命中后 embedding 同时覆盖标题语义

      # 2. 构建最终 chunk 对象（只保留入库需要的字段，body 已并入 content 故丢弃）
      assemble_chunk = {
        "title": title,            # 切片标题（可能带 "- 1" 分块序号）
        "file_title": file_title,  # 所属文件名
        "parent_title": parent_title,  # 父标题（溯源章节）
        "content": content,        # 实际送去 embedding 的文本
      }

      # 3. 判断 part 是否存在（切分/合并过的块才有；普通块不带，保持元数据干净）
      if "part" in chunk:
        assemble_chunk['part'] = chunk.get('part')

      chunks.append(assemble_chunk)

    return chunks  # [{title, file_title, parent_title, content, part?}, ...] 供写入 state['chunks']



  # 输出切分统计：纯日志，方便人工核对切分效果（切了多少块、标题长啥样），不影响业务数据
  def _log_summary(self, raw_content: str, chunks: List[dict], max_length: int):
    """输出切分统计信息"""
    self.log_step("step5", "输出统计")

    lines_count = raw_content.count("\n") + 1  # 行数=换行符数+1（最后一行没有换行符）
    self.logger.info(f"原文档行数: {lines_count}")
    self.logger.info(f"最终切分章节数: {len(chunks)}")
    self.logger.info(f"最大切片长度: {max_length}")

    if chunks:
      self.logger.info("章节预览:")
      for i, sec in enumerate(chunks[:5]):  # 只预览前5个，避免日志刷屏
        title = sec.get("title", "")[:30]  # 标题截断到30字符
        self.logger.info(f"  {i + 1}. {title}...")
      if len(chunks) > 5:  # 剩余的只报个数
        self.logger.info(f"  ... 还有 {len(chunks) - 5} 个章节")

  # 备份切片结果到 chunks.json：调试/排障用，失败只警告不抛异常（备份是辅助功能，不能拖垮主流程）
  def _backup_chunks(self, state: ImportGraphState, sections: List[dict]):
    """将切分结果备份到 JSON 文件"""
    self.log_step("step6", "备份切片")

    local_dir = state.get("file_dir", "")  # 从 state 取输出目录
    if not local_dir:  # 没配目录就直接跳过（比如内容来自数据库而非本地文件）
      self.logger.debug("未设置 file_dir，跳过备份")
      return

    try:
      os.makedirs(local_dir, exist_ok=True)  # 目录不存在则创建，已存在不报错
      output_path = os.path.join(local_dir, "chunks.json")  # 固定文件名，方便下游/人工查看
      with open(output_path, "w", encoding="utf-8") as f:
        json.dump(sections, f, ensure_ascii=False, indent=2)  # ensure_ascii=False 保留中文原文
      self.logger.info(f"已备份到: {output_path}")
    except Exception as e:  # 兜底：磁盘满、权限不足等都不让主流程中断
      self.logger.warning(f"备份失败: {e}")



if __name__ == '__main__':
  setup_logging()

  document_node = DocumentSplitNode()
  # 构造状态字典
  file_path = r"C:\Users\14207\Desktop\doc\temp_dir\万用表RS-12的使用\auto\万用表RS-12的使用_new.md"
  with open(file_path, "r", encoding="utf-8") as f:
      content = f.read()

  state = {
      "file_title": "万用表的使用",
      "md_content": content,
      "file_dir": r"C:\Users\14207\Desktop\doc\temp_dir\万用表RS-12的使用\auto"
  }
  document_node.process(state)