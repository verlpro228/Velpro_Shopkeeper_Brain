import os
import json
from typing import Dict, List, Any
from pathlib import Path

from knowledge.processor.import_process.base import BaseNode, setup_logging
from knowledge.processor.import_process.exceptions import ValidationError, EmbeddingError
from knowledge.processor.import_process.state import ImportGraphState
from knowledge.processor.import_process.config import get_config
from knowledge.utils.client.ai_clients import AIClients


class BgeEmbeddingChunksNode(BaseNode):
  """
  BgeEmbeddingChunksNode 主要职责：
  1. 获取所有的 chunks 拼接要向量的内容
  2. 批量嵌入 chunk 的（embedding_content: item_name + chunk.get('content')）
  3. 将所有 chunk 嵌入后的向量值，存储到列表中，在返回给下一个节点用
  """
  name = "bge_embedding_node"

  def process(self,state:ImportGraphState)->ImportGraphState:
    # 参数校验
    validated_chunks, config = self._validate_get_inputs(state)

    # 获取批量嵌入的阈值
    embedding_batch_chunk_size = getattr(config,"embedding_batch_chunk_size",16)

    # 准备分批嵌入
    total_length = len(validated_chunks)
    final_chunks = []
    
    # range(起点, 终点, 步长)：i 按批大小跳跃（0/16/32...），每轮代表当前批的首个 chunk 下标
    for i in range(0,total_length,embedding_batch_chunk_size):
      # 切片取 [i, i+批大小) 一批；尾批不足 16 个时切片自动截到末尾，无需特判
      batch = validated_chunks[i:i+embedding_batch_chunk_size]
      # 拼接要嵌入的内容，向量嵌入，把嵌入的向量注入到 chunk 中（传 i/total_length 用于打进度日志）
      batch_chunks = self._process_batch_chunks(batch, i, total_length)
      # extend 摊平追加：避免 append 造成"列表套列表"的嵌套结构
      final_chunks.extend(batch_chunks)

    # 更新&返回state
    state["chunks"] = final_chunks

    return state

  
  def _process_batch_chunks(self,batch:List[Dict[str,Any]],start_index:int,total_length:int):
    """处理一个批次的切片"""
    self.log_step("step2", f"开始批量处理chunk嵌入:批次{start_index + 1}-{start_index + len(batch)}")

    # 循环处理所有 chunk 的要嵌入的内容拼接
    embedding_contents = []
    for _,chunk in enumerate(batch):  
      content = chunk.get("content")  # 切片正文（标题+body，切分节点产出）
      item_name = chunk.get('item_name')  # 商品名（上游识别节点回填）
      embedding_content = f"{item_name}\n{content}"  # 换行拼接，避免商品名和正文首句粘连
      embedding_contents.append(embedding_content)    # 攒成本批的文本列表，下一步批量送模型

    # 批量嵌入
    try:
      bge_m3_model = AIClients.get_bge_m3_client()
      embedding_result = bge_m3_model.encode_documents(documents=embedding_contents) #将文档转成向量

      if not embedding_result:
        self.logger.warning(f"嵌入后的结果不存在...")
        return batch
    except Exception as e:
      self.logger.warning(f"嵌入向量嵌入失败...{str(e)}")
      return batch
      
    # 循环处理所有 chunk 的向量以及注入到每一个 chunk 中
    for index,chunk in enumerate(batch):
      # 获取稠密向量
      # `.tolist()` 是把 NumPy 数组 → Python 原生 list 的转换方法。
      dense_vector = embedding_result["dense"][index].tolist()     
      # 解析 CSR 稀疏矩阵：所有文本的非零元素拉平存在共享数组里，indptr 记录每条的起止边界
      csr_array = embedding_result['sparse']
      ind_ptr = csr_array.indptr  # 行指针：第 index 条的区间 = ind_ptr[index] → ind_ptr[index+1]
      start_ind_ptr = ind_ptr[index]  # 本条在共享数组中的起点
      end_ind_ptr = ind_ptr[index + 1]  # 本条在共享数组中的终点
      token_id = csr_array.indices[start_ind_ptr:end_ind_ptr].tolist()  # 切出本条的词表 id
      weight = csr_array.data[start_ind_ptr:end_ind_ptr].tolist()  # 切出对应的权重
      sparse_vector = dict(zip(token_id, weight))  # 拼成 {词id: 权重}——Milvus 稀疏向量标准格式

      chunk['dense_vector'] = dense_vector  # 注入稠密向量（语义检索用）
      chunk['sparse_vector'] = sparse_vector  # 注入稀疏向量（关键词精确匹配用），下游入库节点直接取

    self.logger.info(f"开始批量处理chunk嵌入:批次{start_index + 1}-{start_index + len(batch)}/{total_length}")
    return batch    
    

  # 校验输入的参数
  def _validate_get_inputs(self,state:ImportGraphState):
    config = get_config()
    self.log_step("step1","参数校验")
    chunks = state.get("chunks")
    if not chunks or not isinstance(chunks,list):
      raise ValidationError(f"chunks为空或者无效",self.name)
    self.logger.info(f"嵌入的块数：{len(chunks)}")
    return chunks, config
      


if __name__ == '__main__':
  setup_logging()

  base_temp_dir = Path(r"C:\Users\14207\Desktop\doc\temp_dir\万用表RS-12的使用\auto")

  input_path = base_temp_dir / "chunks.json"
  output_path = base_temp_dir / "chunks_vector.json"

  # 1. 读取上游状态
  if not input_path.exists():
    print(f"找不到输入文件: {input_path}")
    exit(1)

  with open(input_path, "r", encoding="utf-8") as f:
    content = json.load(f)   # 把文件里的 JSON 文本 解析成 Python 对象 。

  # 2. 构建模拟的图状态
  state = {
    "chunks": content
  }

  # 3. 触发节点执行
  node_bge_embedding = BgeEmbeddingChunksNode()
  proceed_result = node_bge_embedding.process(state)

  # 4. 结果落盘
  with open(output_path, "w", encoding="utf-8") as f:
    json.dump(proceed_result, f, ensure_ascii=False, indent=4)

  print(f"向量生成测试完成！结果已成功备份至:\n{output_path}")