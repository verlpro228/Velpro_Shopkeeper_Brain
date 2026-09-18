from typing import List, Dict, Any, Tuple

from knowledge.processor.query_process.base import BaseNode, setup_logging
from knowledge.processor.query_process.state import QueryGraphState


class RrfNode(BaseNode):
  # 倒序融合排序 RRF将向量检索和HyDE检索进行 RRF融合排序
  name = "rrf"

  def process(self, state: QueryGraphState) -> Dict[str, Any]:
    # 获取向量检索结果和HyDE检索结果 进行数据校验
    embedding_chunks: List[Dict[str, Any]] = state.get("embedding_chunks") or []
    hyde_embedding_chunks: List[Dict[str, Any]] = state.get("hyde_embedding_chunks") or []

    # 统一格式化
    embedding_chunks_result: List[Dict[str, Any]] = self._normalize_input(embedding_chunks)
    hyde_embedding_chunks_result: List[Dict[str, Any]] = self._normalize_input(hyde_embedding_chunks)

    # 设置每路权重
    rrf_inputs: List[Tuple[List[Dict[str, Any]], float]] = [(embedding_chunks_result, 1.0),(hyde_embedding_chunks_result, 1.0)]

    # RRF计算
    rrf_results: List[Tuple[Dict[str, Any], float]] = self._rrf_merge(rrf_inputs)

    return {
      "rrf_chunks": [entity for entity, _ in rrf_results]
    }

  # 格式化输入数据
  def _normalize_input(self, chunks_input: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    统一格式化输入数据
    输入：各路检索结果
      chunks_input: List[Dict[str, Any]]
      [
        {'pk':1, 'distance':0.7, 'entity':{'chunk_id':1, 'content':'xxxx', 'item_name':'aaa', 'title':'bbb'}},
        {'pk':2, 'distance':0.7, 'entity':{'chunk_id':2, 'content':'xxxx', 'item_name':'aaa', 'title':'bbb'}}
      ]
    输出：格式化的数据结果
      [
        {'chunk_id':1, 'content':'xxxx', 'item_name':'aaa', 'title':'bbb'},
        {'chunk_id':2, 'content':'xxxx', 'item_name':'aaa', 'title':'bbb'}
      ]
    """
    entity_result: List[Dict[str, Any]] = []
    for chunk in chunks_input:
      if not isinstance(chunk, Dict):
        continue
      entity = chunk.get("entity")
      if not isinstance(entity, Dict):
        continue
      entity_result.append(entity)

    return entity_result

  def _rrf_merge(self, rrf_inputs: List[Tuple[List[Dict[str, Any]], float]], rrf_k: int = 60, top_k: int = 5) -> List[
    Tuple[Dict[str, Any], float]]:
    """
      利用RRF公式进行倒序融合排序
      rrf公式：  weight / (k + index)

      rrf_inputs : List[tuple[List[str,Any],float]]        元组：每路集合数据对应rrf权重值
      输入：
        [
          ([
              {'chunk_id':1, 'content':'xxxx', 'item_name':'aaa', 'title':'bbb'},
              {'chunk_id':2, 'content':'xxxx', 'item_name':'aaa', 'title':'bbb'}
            ],1.0),
          ([
              {'chunk_id':1, 'content':'xxxx', 'item_name':'aaa', 'title':'bbb'},
              {'chunk_id':2, 'content':'xxxx', 'item_name':'aaa', 'title':'bbb'}
            ],1.0)
        ]
    输出：  列表元组：   文档 -> 各路分数和
      [
        ({'chunk_id':1, 'content':'xxxx', 'item_name':'aaa', 'title':'bbb'},0.9),
        ({'chunk_id':2, 'content':'xxxx', 'item_name':'aaa', 'title':'bbb'},0.8)
      ]
    """
    # 利用RRF公式进行倒序融合排序
    entity_scores = {}  # 用于存储每个文档累计分数 { 1:0.5, 2:0.2 }
    entity_data = {}  # 用于存储每个文档信息    {1:{'chunk_id':1,'':’’...},2:{'chunk_id':2,'':''...}}
    for chunks_input, weight in rrf_inputs:
      for index, entity in enumerate(chunks_input):
        chunk_id = entity.get("chunk_id")
        entity_scores[chunk_id] = entity_scores.get(chunk_id, 0.0) + weight / (rrf_k + (index + 1))
        entity_data.setdefault(chunk_id, entity)  # 存储文档信息 不存在则写入 存在则不写入

    # 排序截断  top_k
    rrf_result = [
      (entity_data[item[0]], item[1])
      for item in sorted(entity_scores.items(), key=lambda item: item[1], reverse=True)
    ]
    return rrf_result[:top_k]


if __name__ == "__main__":
  setup_logging()
  print("=" * 60)
  print("开始测试: RRF 融合节点")
  print("=" * 60)

  # 模拟两路检索结果
  # chunk_1 命中 2 路（预期最高分）
  # chunk_2 命中 2 路
  # chunk_3, chunk_4 各命中 1 路
  mock_state = {
    "embedding_chunks": [
      {"entity": {"chunk_id": "chunk_1", "content": "向量搜索结果#1"}},
      {"entity": {"chunk_id": "chunk_2", "content": "向量搜索结果#2"}},
      {"entity": {"chunk_id": "chunk_3", "content": "向量搜索结果#3"}},
    ],
    "hyde_embedding_chunks": [
      {"entity": {"chunk_id": "chunk_2", "content": "HyDE搜索结果#1"}},
      {"entity": {"chunk_id": "chunk_1", "content": "HyDE搜索结果#2"}},
      {"entity": {"chunk_id": "chunk_4", "content": "HyDE搜索结果#3"}},
    ],
  }

  print("【输入状态】:")
  print(f"  embedding_chunks: {len(mock_state['embedding_chunks'])} 条")
  print(f"  hyde_embedding_chunks: {len(mock_state['hyde_embedding_chunks'])} 条")
  print("-" * 60)

  rrf_node = RrfNode()
  result = rrf_node.process(mock_state)

  print("\n【融合结果】:")
  for i, chunk in enumerate(result["rrf_chunks"], 1):
    print(f"[{i}] {chunk.get('chunk_id')} - {chunk.get('content')}")

  print("-" * 60)
  print("测试完成")
