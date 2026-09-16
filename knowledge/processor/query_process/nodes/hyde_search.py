"""HyDE 检索节点

使用 Hypothetical Document Embedding 技术：
先让 LLM 生成假设性文档，再将其与原查询拼接后向量化检索，提升召回质量。

节点数据流（①→⑥）:
  ① 校验 state 输入（rewritten_query 必填str / item_names 必填list）
  ② LLM 按商品名+问题生成"假设性文档"（LLM 失败降级为空串）
  ③ 原查询 + 假设文档拼接 → BGE-M3 产出稠密+稀疏双向量
  ④ item_names → 构建 Milvus 过滤表达式 item_name in [...]
  ⑤ kb_chunks_v1 混合检索（双路 ANN + WeightedRanker 加权融合）
  ⑥ 结果写入 state['hyde_embedding_chunks']（dict 形式只合并此字段）

上游: item_name_confirm（产出 rewritten_query / item_names）
下游: RRF 融合节点（与普通向量检索结果融合去重）
"""

import json
import logging
from typing import List, Tuple, Union, Any, Dict

from langchain_core.messages import SystemMessage, HumanMessage

from knowledge.processor.query_process.state import QueryGraphState
from knowledge.processor.query_process.base import BaseNode
from knowledge.processor.query_process.exceptions import StateFieldError
from knowledge.prompt.query_prompt import HYDE_USER_PROMPT_TEMPLATE
from knowledge.utils.client.ai_clients import AIClients
from knowledge.utils.client.storage_clients import StorageClients
from knowledge.utils.embedding_util import generate_bge_m3_hybrid_vectors
from knowledge.utils.milvus_util import create_hybrid_search_requests, execute_hybrid_search_query

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class HyDeSearchNode(BaseNode):
  """HyDE 检索节点

  流程: 参数校验 → LLM 生成假设文档 → 拼接原查询 → 向量化 → 混合检索
  """
  name = "search_embedding_hyde"

  def process(self, state: QueryGraphState) -> Union[QueryGraphState, Dict[str, Any]]:
    """执行 HyDE 检
    Args:
        state: 需包含 rewritten_query 和 item_names
    Returns:
        {"hyde_embedding_chunks": [...]} 搜索结果列表
    """
    
    # 1. 参数校验
    validated_query, validate_item_names = self._validate_query_inputs(state)

    # 2. 生成假设性文档（LLM 失败时返回 ""，降级为仅用原查询检索，不中断流程）
    hy_document = self._generate_hy_document(validated_query, validate_item_names)

    # 3. 获取嵌入模型 & milvus 客户端
    embedding_model = AIClients.get_bge_m3_client()
    milvus_client = StorageClients.get_milvus_client()
    if not embedding_model or not milvus_client:
      return state

    # 4. 假设性文档嵌入(注入问题+假设性文档)
    # 拼接策略: 原查询在前主导语义，假设文档在后补充领域术语，弥补"问句≠文档"的向量鸿沟
    embedding_document = f"{validated_query}\n{hy_document}"
    embedding_result = generate_bge_m3_hybrid_vectors(
      embedding_model,
      embedding_documents=[embedding_document]
    )

    if not embedding_result:
      return state

    # 5. 获取 item_name 的过滤表达式（把检索范围限定在已确认商品的 chunks 内）
    item_name_filtered_expr = self._item_name_filte_expr(validate_item_names)

    # 6. 创建混合搜索请求
    hybrid_search_requests = create_hybrid_search_requests(
      dense_vector=embedding_result['dense'][0],
      sparse_vector=embedding_result['sparse'][0],
      expr=item_name_filtered_expr
    )

    # 7. 执行混合搜索请求（单查询 → reps[0] 即唯一结果集，元素为 {distance, entity}）
    reps = execute_hybrid_search_query(
      milvus_client,
      collection_name=self.config.chunks_collection,
      search_requests=hybrid_search_requests,
      norm_score=True,
      output_fields=["chunk_id", "content", "item_name", 'title']
    )

    if not reps or not reps[0]:
      return state

    # 8. 只更新 hyde_embedding_chunks（返回 dict，LangGraph 仅合并该键，不覆盖其他 state 字段）
    return {"hyde_embedding_chunks": reps[0]}

  def _validate_query_inputs(self, state: QueryGraphState) -> Tuple[str, List[str]]:
    """校验输入参数（缺失或类型不符 → 抛 StateFieldError 快速失败）"""
    rewritten_query = state.get('rewritten_query', "")
    item_names = state.get('item_names', "")

    if not rewritten_query or not isinstance(rewritten_query, str):
      raise StateFieldError(
        node_name=self.name,
        field_name="rewritten_query",
        expected_type=str
      )

    if not item_names or not isinstance(item_names, list):
      raise StateFieldError(
        node_name=self.name,
        field_name="item_names",
        expected_type=list
      )

    return rewritten_query, item_names

  def _item_name_filte_expr(self, validate_item_names: List[str]) -> str:
    """构建商品名过滤表达式
    例: ["A", "B"] → ' item_name in ["A", "B"]'（Milvus 表达式，值用双引号）
    """
    quoted = ", ".join(f'"{v}"' for v in validate_item_names)
    return f" item_name in [{quoted}]"

  def _generate_hy_document(self, validated_query: str, validate_item_names: List[str]) -> str:
    """使用 LLM 生成假设性文档（核心降级点: 任何失败都返回 ""，不抛异常）"""

    # 1. 获取 LLM 客户端（非流式）
    llm_client = AIClients.get_llm_openai(False)

    # 2. 判断（客户端不可用 → 空串降级，等效普通向量检索）
    if llm_client is None:
      return ""

    # 3. 获取系统提示词以及用户提示词
    user_prompt = HYDE_USER_PROMPT_TEMPLATE.format(
      item_names='、'.join(validate_item_names),
      rewritten_query=validated_query
    )
    system_prompt = f"您是一位{'、'.join(validate_item_names)}的技术文档领域的专家，主要擅长编写技术文档、操作手册、文档规格说明"

    try:
      # 4. 获取 AIMessage
      llm_response = llm_client.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt)
      ])

      # 5. 获取内容
      llm_response_content = getattr(llm_response, 'content', "").strip()

      # 6. 判断是否存在
      if not llm_response_content:
        return ""

      return llm_response_content

    except Exception as e:
      self.logger.error(f"LLM调用失败:{str(e)}")
      return ""


# ================================================================== #
#                        测试入口                                   #
# ================================================================== #

if __name__ == "__main__":
  from knowledge.processor.query_process.base import setup_logging

  setup_logging()

  print("=" * 60)
  print("开始测试: HyDE 检索节点 (HydeSearchNode)")
  print("=" * 60)

  mock_state = {
    # 注意: item_names 必须与 kb_chunks_v1 中 item_name 字段值完全一致，否则过滤后 0 条
    "rewritten_query": "Brother HAK 180 烫金机 使用时有哪些安全注意事项",
    "item_names": ["Brother HAK 180 烫金机"],
  }

  print("【输入状态】:")
  print(f"  查询: {mock_state['rewritten_query']}")
  print(f"  商品: {mock_state['item_names']}")
  print("-" * 60)

  node = HyDeSearchNode()
  result = node(mock_state)

  chunks = result.get("hyde_embedding_chunks", [])
  print(f"\n【HyDE 检索结果】: {len(chunks)} 条")
  for i, chunk in enumerate(chunks, 1):
    entity = chunk.get("entity", {})
    print(f"  [{i}] chunk_id={entity.get('chunk_id')} "
          f"item_name={entity.get('item_name')} "
          f"distance={chunk.get('distance', 'N/A')}")
    content = entity.get("content", "")
    print(f"      内容: {content[:80]}...")

  print("-" * 60)
  print("测试完成")
