from typing import List, Tuple, Dict
import json
import os
from langchain_core.messages import HumanMessage, SystemMessage
from pymilvus import DataType

from knowledge.processor.import_process.base import BaseNode,setup_logging
from knowledge.processor.import_process.exceptions import StateFieldError, ValidationError
from knowledge.processor.import_process.state import ImportGraphState
from knowledge.prompt.import_prompt import ITEM_NAME_USER_PROMPT_TEMPLATE, ITEM_NAME_SYSTEM_PROMPT
from knowledge.utils.client.ai_clients import AIClients
from knowledge.utils.client.storage_clients import StorageClients


class ItemNameRecognitionNode(BaseNode):
  name = "item_name_recognition_node"

  def process(self, state: ImportGraphState) -> ImportGraphState:
    # 1. 参数校验
    file_title, chunks, item_name_chunks_k, item_name_chunk_size = self._validate_state(state)

    # 2. 构建商品名识别上下文
    item_name_recognition_context = self._prepare_item_name_recognition_context(
      chunks, item_name_chunks_k, item_name_chunk_size
    )

    # 3. LLM商品名识别
    item_name = self._recognition_name(file_title, item_name_recognition_context)

    # 4. 向量化提取到商品名
    dense_vector, sparse_vector = self._embedding_item_name(item_name)

    # 5. 存储到milvus中
    self._insert_milvus(file_title, item_name, dense_vector, sparse_vector, self.config.item_name_collection)

    # 6. 回填item_name信息
    self._fill_item_name(item_name, state, chunks)

    # 7.备份，给下个节点准备下测试数据。
    self._backup_chunks(state, chunks)

    return state

  # 校验 state 字段
  def _validate_state(self, state: ImportGraphState) -> Tuple[str, List, int, int]:
    # 1. 从 state 取入参：file_title 用于商品名校验，chunks 是上一切分节点产出（识别上下文来源）
    file_title = state.get("file_title")
    chunks = state.get("chunks")

    # 2. 校验上游 state 字段：缺 file_title 无法锚定商品，缺/非列表的 chunks 没有识别素材
    if not file_title:
      raise StateFieldError(node_name=self.name, field_name="file_title", expected_type=str)
    if not chunks or not isinstance(chunks, list):
      raise StateFieldError(node_name=self.name, field_name="chunks", expected_type=list)

    # 3. 读识别用配置：item_name_chunk_k = 取前 k 个 chunk 拼上下文，item_name_chunk_size = 每个 chunk 截断字符数
    item_name_chunks_k = self.config.item_name_chunk_k
    if not item_name_chunks_k or item_name_chunks_k <= 0:
      raise ValidationError(message="item_name_chunk_k为空或者无效", node_name=self.name)

    item_name_chunk_size = self.config.item_name_chunk_size
    if not item_name_chunk_size or item_name_chunk_size <= 0:
      raise ValidationError(message="item_name_chunk_size为空或者无效", node_name=self.name)

    return file_title, chunks, item_name_chunks_k, item_name_chunk_size

  # 构建商品名识别上下文
  def _prepare_item_name_recognition_context(self, chunks, item_name_chunks_k, item_name_chunk_size) -> str:
    # 1. 准备累加器：total 累计已拼字符数（用于控总量），final_context 收集每条切片前缀化后的文本
    total = 0
    final_context = []
    # 2. 只取前 k 个 chunk 拼上下文（商品名一般出现在文档开头，避免喂全文浪费 token）
    for index, chunk in enumerate(chunks[:item_name_chunks_k]):
      # 3. 防御非 dict 元素（state 来源复杂，跳过避免 chunk.get 崩）
      if not isinstance(chunk, dict):
        continue
      chunk_content = chunk.get('content')
      # 4. 用【切片】-index 标记位置 + 序号，让 LLM 看到结构化输入（也方便回溯是哪一片）
      context = f"【切片】-{index}-{chunk_content}"
      # 5. 容量护栏：再拼一条就超阈值则停止，保证最终 context 不超过 item_name_chunk_size
      if total + len(context) > item_name_chunk_size:
        break

      total += len(context)
      final_context.append(context)
    # 6. 用换行拼成多行串，作为 LLM 识别的 prompt 上下文
    return "\n".join(final_context)

  # 商品名识别
  def _recognition_name(self, file_title, item_name_recognition_context) -> str:
    # 整体思路：调一次 LLM 让它从"文件标题 + 切片上下文"里提炼商品名称；
    # 任意环节失败/识别不出，都降级用 file_title，保证下游总能拿到非空商品名
    try:
      # 1. 取 LLM 客户端；response_format=False 关闭 JSON 模式，让模型直接吐纯文本商品名
      llm_client = AIClients.get_llm_openai(response_format=False)
      # 2. 用 prompt 模板把 file_title 和上下文填进 user prompt（system prompt 单独固定）
      user_prompt = ITEM_NAME_USER_PROMPT_TEMPLATE.format(
        file_title=file_title, context=item_name_recognition_context
      )

      # 3. 双消息调用：SystemMessage 立人设/规则，HumanMessage 提本次需求
      llm_response = llm_client.invoke([
        SystemMessage(content=ITEM_NAME_SYSTEM_PROMPT),
        HumanMessage(content=user_prompt)
      ])

      # 4. 去首尾空白得到纯识别结果
      llm_result = llm_response.content.strip()
      # 5. 兜底分支：空串 / "UNKNOWN" 都视为"识别失败"，降级用文件标题（保证返回非空）
      if not llm_result or llm_result == "UNKNOWN":
        self.logger.info(f"LLM未识别出商品名，降级使用标题: {file_title}")
        return file_title

      # 6. 正常路径：返回 LLM 识别到的商品名
      self.logger.info(f"LLM提取到商品名: {llm_result}")
      return json.loads(llm_result).get("item_name")  #json.loads() 就是把 JSON 格式的字符串转换成 Python 对象的方法。
    except Exception as e:
      # 7. 异常兜底：网络/限流/模型超时等任何异常都吞掉，降级用标题，不让主流程因 LLM 失败而中断
      self.logger.error(f"LLM调用失败，降级使用标题: {file_title}，异常: {e}")
      return file_title


  # 商品名向量化（BGE-M3 一次产出 dense + sparse 双路向量，供 Milvus 混合检索）
  def _embedding_item_name(self, item_name) -> Tuple[List[float], Dict[int, float]]:
    try:
      # 1. 取统一管理的 BGE-M3 客户端（避免重复加载模型）
      bge_m3_client = AIClients.get_bge_m3_client()
      # 2. 批量编码接口传列表（这里只有 1 条），返回 {dense: ndarray, sparse: CSR 稀疏矩阵}
      vector_result = bge_m3_client.encode_documents([item_name])

      # 3. dense 向量：取第 0 条转 list，便于写入 Milvus
      dense_vector = vector_result['dense'][0].tolist()
      # 4. 解析 CSR 稀疏矩阵：indptr 是行指针，[0]→[1] 即本条 item_name 在 indices/data 中的起止下标
      start_index = vector_result['sparse'].indptr[0]
      end_index = vector_result['sparse'].indptr[1]
      # 5. 按起止下标切出本条的 token_id 和对应权重
      token_id = vector_result['sparse'].indices[start_index:end_index].tolist()
      weight = vector_result['sparse'].data[start_index:end_index].tolist()
      # 6. zip 成 {词表id: 权重} 字典——Milvus 稀疏向量的标准格式
      sparse_vector = dict(zip(token_id, weight))

      return dense_vector, sparse_vector
    except ConnectionError as e:
      # 客户端连不上（模型服务未启动等），单独分类便于排障
      self.logger.error(f"BGE-M3 客户端获取失败: {e}")
      return None, None
    except Exception as e:
      # 其他异常降级返回 (None, None)，由下游插入节点决定如何处理
      self.logger.error(f"商品名 [{item_name}] 向量化处理失败: {e}")
      return None, None

  # 商品名写入 Milvus（file_title 与 item_name 双向量入库，供后续"商品名→文档"检索）
  def _insert_milvus(self, file_title, item_name, dense_vector, sparse_vector, item_name_collection):
    # 1. 前置守卫：上游向量化失败会返回 (None, None)，这里拒收残缺数据，避免脏数据入库
    if not dense_vector or not sparse_vector:
      self.logger.error(f"文档{file_title} 对应的商品名{item_name} 向量生成不完整")
      return

    # 2. 单独 try 获取客户端：连接失败直接退出，与后面的写入异常分开便于定位
    try:
      milvus_client = StorageClients.get_milvus_client()
    except Exception as e:
      self.logger.error(f"Milvus 客户端创建失败: {e}")
      return

    try:
      # 3. 集合不存在则先建（首次导入时自动初始化 schema/索引），保证 insert 不报"集合不存在"
      # has_collection 是 Milvus 官方内置的方法，用来判断某个 collection 是否存在。
      if not milvus_client.has_collection(item_name_collection):
        self._create_item_name_collection(item_name_collection, milvus_client)

      # 4. 组装一行记录：两个标量字段（溯源用）+ 两个向量字段（混合检索用）
      data = {
        "file_title": file_title,
        "item_name": item_name,
        "dense_vector": dense_vector,
        "sparse_vector": sparse_vector
      }
      # 5. insert 接受列表（可批量），这里单条；返回的 ids 是 Milvus 自生成的主键
      result = milvus_client.insert(collection_name=item_name_collection, data=[data])
      self.logger.info(f"已成功保存到 Milvus，ID: {result['ids'][0]}")
    except Exception as e:
      # 6. 建集合/写入失败只记日志不抛出：商品名入库失败不应中断整个文档导入流程
      self.logger.error(f"Milvus 数据操作失败: {e}")

  # 创建商品名集合（定义 schema + 建索引，仅首次导入时由 _insert_milvus 触发）
  def _create_item_name_collection(self, collection_name, milvus_client):
    # 1. 定义表结构：1 主键 + 2 标量字段 + 2 向量字段
    schema = milvus_client.create_schema()
    schema.add_field(field_name="pk", datatype=DataType.VARCHAR, is_primary=True, auto_id=True,
                     max_length=100)  # 主键：VARCHAR 型自增 ID
    schema.add_field(field_name="file_title", datatype=DataType.VARCHAR, max_length=65535)  # 溯源：商品名所属文档
    schema.add_field(field_name="item_name", datatype=DataType.VARCHAR, max_length=65535)  # 商品名原文
    schema.add_field(field_name="dense_vector", datatype=DataType.FLOAT_VECTOR, dim=1024)  # 稠密向量，dim 须与 BGE-M3 输出一致
    schema.add_field(field_name="sparse_vector", datatype=DataType.SPARSE_FLOAT_VECTOR)  # 稀疏向量（无需指定维度）

    # 2. 为两个向量字段分别建索引：dense 用 AUTOINDEX+余弦相似度，sparse 用倒排索引+内积
    # 用来生成一个"索引参数配置器"（对象），用于定义集合的索引参数。
    index_param = milvus_client.prepare_index_params()
    index_param.add_index(field_name="dense_vector", index_name="dense_vector_index",
                          index_type="AUTOINDEX", metric_type="COSINE")  # COSINE：按向量夹角衡量语义相似度
    index_param.add_index(field_name="sparse_vector", index_name="sparse_vector_index",
                          index_type="SPARSE_INVERTED_INDEX", metric_type="IP")  # IP 内积：稀疏向量标准度量

    # 3. schema + 索引一次性提交建集合
    milvus_client.create_collection(collection_name=collection_name,
                                    schema=schema, index_params=index_param)
    self.logger.info(f"集合 {collection_name} 创建成功并构建了索引")

  # 回填商品名：把识别结果写进每个 chunk 和 state，供下游节点（向量化入库等）直接使用
  def _fill_item_name(self, item_name, state, chunks):
    # 1. 逐片打标：chunks 是字典列表且与 state['chunks'] 同一引用，改这里即改 state
    for chunk in chunks:
      chunk['item_name'] = item_name
    # 2. state 顶层也记一份：下游不遍历 chunks 也能拿到商品名（如日志、检索过滤）
    state['item_name'] = item_name


  def _backup_chunks(self, state, chunks):
    """
    将回填item_name的切片列表结果备份到json文件，给下个节点单元测试使用。
    :param state:
    :param chunks:
    :return:
    """
    # 1. 取输出目录：内容来自数据库等场景没有 file_dir，直接跳过备份
    local_dir = state.get("file_dir", "")
    if not local_dir:
      return

    # 2. 目录不存在则创建（exist_ok=True：如果目录已存在，不会抛出异常，直接跳过），拼固定文件名便于下游/人工定位
    os.makedirs(local_dir, exist_ok=True)
    output_path = os.path.join(local_dir, "chunks_item_name.json")
    try:
      # 3. 落盘：ensure_ascii=False 保留中文原文，indent=4 便于人工阅读
      with open(output_path, "w", encoding="utf-8") as f:
        json.dump(chunks, f, ensure_ascii=False, indent=4)
    except Exception as e:
      # 4. 备份是辅助功能：失败只警告不抛出，不能拖垮导入主流程
      self.logger.warning(f"备份失败：{e}")




if __name__ == '__main__':
    setup_logging()

    # 1. 读取chunk.json
    chunk_json_path = r"C:\Users\14207\Desktop\doc\temp_dir\万用表RS-12的使用\auto\chunks.json"
    with open(chunk_json_path, "r", encoding="utf-8") as f:
        chunk_content = json.load(f)

    # 2. 构建state
    state = {
        "file_dir": r"C:\Users\14207\Desktop\doc\temp_dir\万用表RS-12的使用\auto",
        "file_title": "万用表的使用",
        "chunks": chunk_content
    }

    # 3. 实例化节点
    node = ItemNameRecognitionNode()

    # 4. 调用process
    result = node(state)

    # 5. 输出结果
    print(f"商品名: {result.get('item_name')}")
    print(f"chunks数量: {len(result.get('chunks', []))}")
    print(f"首个chunk是否含item_name: {'item_name' in result['chunks'][0]}")