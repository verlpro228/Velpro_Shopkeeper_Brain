import re
from typing import Any, Dict, List, Tuple

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
    final_chunks:List[Dict[str,Any]] = self.split_and_merge(sections, max_content_length, min_content_length)

    # 4. 组装
    chunks = self._assemble_chunk(final_chunks)

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
    heading_re = re.compile(f"^\s*(#{1,6})\s+.+")  #匹配标题正则表达式；括号捕获#串，便于数长度得等级
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


  def merge_short_section(self, current_sections: List[Dict[str, Any]],min_content_length: int):
      """
      贪心累加算法合并过短的章节

      局限性：
      1. 撑破最小的阈值：大一点不用管
      2. 孤儿小块：也不用管（大量都是小块）
      """
      # 1. 定义变量
      current_section = current_sections[0]
      final_sections = []  # 最终的箱子

      # 2. 遍历以及合并
      for next_section in current_sections[1:]:
          # 同源检查
          same_parent = (current_section['parent_title'] == next_section['parent_title'])

          if same_parent and len(current_section.get('body')) < min_content_length:
              # body的合并
              current_section['body'] = (
                  current_section.get('body').rstrip() + "\n\n" + next_section.get('body').lstrip()
              )
              # 更新 current_title
              current_section['title'] = current_section['parent_title']
              current_section['part'] = 0
          else:
              # 将原来 current_section 进行封箱
              final_sections.append(current_section)
              # 更新 next_section
              current_section = next_section

      # 最后一个（封装起来）
      final_sections.append(current_section)

      # 4. 对所有 section 的 part 做处理
      part_counter = {}
      result = []
      for final_section in final_sections:
          if "part" in final_section:
              parent_title = final_section.get('parent_title')
              part_counter[parent_title] = part_counter.get(parent_title, 0) + 1
              new_part = part_counter[parent_title]
              final_section['part'] = new_part
              final_section['title'] = final_section['title'] + f"- {new_part}"

          result.append(final_section)

      return result





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