from typing import List, Dict, Any

from knowledge.processor.query_process.base import BaseNode, setup_logging
from knowledge.processor.query_process.state import QueryGraphState
from knowledge.utils.client.ai_clients import AIClients


class RerankNode(BaseNode):
  # 重排序节点 精排序
  name = "rerank"

  def process(self, state: QueryGraphState) -> QueryGraphState:
    # 重排序节点 精确排序 交叉编码器

    # 1. 获取重写问题或原始问题。 用于交叉编码器的 Key
    rewritten_query = state.get("rewritten_query") or state.get("original_query")

    # 2. 合并多源文档        RRF集合【向量搜索+HyDE】      +        Web MCP 集合
    merge_docs: List[Dict[str, Any]] = self._merge_multi_source_docs(state)

    # 3.Rerank精排  采用BGE Reranker Large模型   ->  交叉编码器进行相关性得分     [CLS]key[SEP]document[SEP]
    rerank_docs: List[Dict[str, Any]] = self._rerank_merged_docs(merge_docs, rewritten_query)

    # 4. 第一断崖检测截断。不要采用固定截断方式。
    cutoff_docs = self.cliff_cutoff(rerank_docs, self.config.rerank_max_top_k, self.config.rerank_min_top_k,
                                    self.config.rerank_gap_abs)

    # 5. 回填
    state['reranked_docs'] = cutoff_docs

    return state

  # 合并多源文档 RRF集合【向量搜索+HyDE】+ Web MCP 集合
  def _merge_multi_source_docs(self, state: QueryGraphState) -> List[Dict[str, Any]]:
    merge_docs: List[Dict[str, Any]] = []
    rrf_chunks = state.get("rrf_chunks")
    for doc in rrf_chunks:
      merge_docs.append({
        "chunk_id": doc.get("chunk_id"),
        "content": doc.get("content"),
        "title": doc.get("title"),
        "item_name": doc.get("item_name"),
        "source": "local"
      })

    web_search_docs = state.get("web_search_docs")
    for doc in web_search_docs:
      merge_docs.append({
        "content": doc.get("content", "") or doc.get("snippet"),  # 兼容性名称匹配
        "title": doc.get("title"),
        "url": doc.get("url"),
        "source": "web"
      })

    return merge_docs

  def _rerank_merged_docs(self, merge_docs: List[Dict[str, Any]], rewritten_query: str) -> List[Dict[str, Any]]:
    """Rerank 精排（带分数的集合实体对象）
    采用BGE Reranker Large 模型 -> 交叉编码器进行相关性得分     [CLS]key[SEP]document[SEP]
    1. 获取Reranker模型客户端  
    2. 准备pairs  [CLS]key[SEP]document[SEP]
      pairs = [
        ["rewritten_query", "doc1"],
        ["rewritten_query", "doc2"],
        ["rewritten_query", "doc3"],
        ...
      ]
    3. 调用reranker模型打分函数:
      scores = reranker.compute_score(sentence_pairs=pairs, normalize=True)  # normalize=True 归一化
    4. 回填分数到每个文档
    5. 根据文档分数进行排序
    6. 返回排序后的文档
    """

    # 1. 获取Reranker模型客户端  
    try:
      reranker_client = AIClients.get_bge_m3_rerank_client()
    except Exception as e:
      self.logger.warning(f"BGE-M3 Reranker模型获取失败 原因:{str(e)}，降级处理，返回原数据")
      # 降级处理  Reranker 失败时返回原序，score 设为 None
      return [{**doc, "score": None} for doc in merge_docs]

    # 2. 准备pairs  [CLS]key[SEP]document[SEP]
    pairs = [[rewritten_query, doc["content"]] for doc in merge_docs]

    # 3. 调用reranker模型打分函数
    scores = reranker_client.compute_score(sentence_pairs=pairs, normalize=True)
    # 单文档防护问题
    # 需要加防护，且与版本无关，这是FlagEmbedding的API设计问题，加一行isinstance检查成本很低但能避免线上崩溃
    if isinstance(scores, (float, int)):
      scores = [scores]

    # 4. 回填分数到每个文档
    rerank_docs = [{**doc, "score": score} for doc, score in zip(merge_docs, scores)]

    # 5. 根据文档分数进行排序
    rerank_docs_sorted = sorted(rerank_docs, key=lambda x: x["score"], reverse=True)

    # 6. 返回排序后的文档
    return rerank_docs_sorted

  # 第一断崖检测截断。不要采用固定截断方式，也不用最大断崖方式，而是一种动态截断方式。
  def cliff_cutoff(self, rerank_docs: List[Dict[str, Any]], rerank_max_top_k: int = 10, rerank_min_top_k: int = 3,
                   rerank_gap_abs: float = 0.15) -> List[Dict[str, Any]]:
    """动态断崖截断

    参数:
      rerank_docs      重排后的文档列表(已按 score 降序)
      rerank_max_top_k 允许返回的最大文档数量
      rerank_min_top_k 允许返回的最小文档数量(保底)
      rerank_gap_abs   判定"断崖"的相邻分数差绝对阈值

    逻辑:
      从头部向后扫描相邻分数落差,第一个 gap >= rerank_gap_abs 的位置即"断崖",
      截断数量被 [min_top_k, max_top_k] 夹住,随每次 query 分数分布动态变化,而非固定 top_k。
    """
    # 上界:即使用户配了更大的 max,最多也只返回实际文档数量
    # （阈值统一走形参，调用方已传入 self.config 的值，避免形参与 self.config 双轨不一致）
    upper_bound = min(rerank_max_top_k, len(rerank_docs))
    # 下界:保底篇数,但也夹住不超过上界,防止 min > max 时越界
    lower_bound = min(rerank_min_top_k, upper_bound)

    # 只有 1 篇(或更少)无从断崖,直接返回
    if upper_bound <= 1:
      return rerank_docs[:upper_bound]

    # 默认截断点=上界,即"没发现断崖就全给"
    cut_off = upper_bound

    # 第一断崖检测:从头扫描相邻分数差,第一个 gap>=阈值处即断崖
    for i in range(0, upper_bound - 1):
      current_score = rerank_docs[i].get("score")
      next_score = rerank_docs[i + 1].get("score")

      # score=None 说明走了降级路径(模型调用失败),跳过该对继续找
      if current_score is None or next_score is None:
        continue

      gap = current_score - next_score  # 降序,current>=next,gap>=0

      if gap >= rerank_gap_abs:
        cut_off = i + 1  # 断崖在第 i、i+1 之间,取前 i+1 篇
        self.logger.info(f"第一断崖位置={cut_off}, gap={gap:.4f}")
        break

    # 断崖如果出现在下界之前(连保底篇数都没给够),则退回保低下界
    cut_off = max(cut_off, lower_bound)
    return rerank_docs[:cut_off]


# ================================================================== #
#                        测试入口                                   #
# ================================================================== #

if __name__ == "__main__":
  from dotenv import load_dotenv

  load_dotenv()
  setup_logging()

  print("=" * 60)
  print("开始测试: 重排序节点 (RerankNode)")
  print("=" * 60)

  mock_state = {
    "rewritten_query": "怎么测这块主板的短路问题？",
    "rrf_chunks": [
      {"chunk_id": "local_1", "title": "主板维修手册",
       "content": "主板短路通常表现为通电后风扇转一下就停，可以使用万用表的蜂鸣档测量。"},
      {"chunk_id": "local_2", "title": "闲聊",
       "content": "今天中午去吃猪脚饭吧，这块主板外观很漂亮。"},
    ],
    "web_search_docs": [
      {"url": "https://example.com/repair", "title": "短路查修指南",
       "snippet": "主板通电前先打各主供电电感的对地阻值，阻值偏低就是短路。"},
      {"url": "https://example.com/news", "title": "科技新闻",
       "snippet": "苹果发布新款手机，A系列芯片性能提升20%。"},
    ],
  }

  print("【输入状态】:")
  print(f"  查询: {mock_state['rewritten_query']}")
  print(f"  本地文档: {len(mock_state['rrf_chunks'])} 篇")
  print(f"  网络文档: {len(mock_state['web_search_docs'])} 篇")
  print("-" * 60)

  node = RerankNode()
  result = node.process(mock_state)

  print("\n【重排序结果】:")
  for i, doc in enumerate(result["reranked_docs"], 1):
    score = doc.get('score')
    score_str = f"{score:.4f}" if score is not None else "N/A"
    print(f"[{i}] score={score_str} | {doc['source']:5} | {doc['content'][:50]}...")

  print("-" * 60)
  print("测试完成")
