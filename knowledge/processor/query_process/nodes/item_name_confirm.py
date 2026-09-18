import json
import re
from typing import Optional, List, Dict, Any, Tuple
from langchain_core.messages import SystemMessage, HumanMessage
from knowledge.utils.client.storage_clients import StorageClients
from pymilvus import AnnSearchRequest

from knowledge.processor.query_process.base import BaseNode
from knowledge.processor.query_process.state import QueryGraphState
from knowledge.prompt.query_prompt import ITEM_NAME_EXTRACT_TEMPLATE
from knowledge.utils.client.ai_clients import AIClients
from knowledge.utils.embedding_util import generate_bge_m3_hybrid_vectors
from knowledge.utils.milvus_util import create_hybrid_search_requests, execute_hybrid_search_query
from knowledge.utils.mongo_history_util import get_recent_messages
from json import JSONDecodeError

"""
商品名提取器
基于用户的原始问题和历史对话提取用户真正想问的商品名
"""

# ============================================================================
# 【节点地图】本文件 = 查询流程第一个节点，回答"用户到底在问哪个商品"
#
# 数据流：
#   original_query + 历史对话(最近10条)
#     → ① ItemNameExtractor：LLM 提取商品名 + 改写问题（失败自动兜底降级）
#     → ② ItemNameAligner._match_vector：BGE-M3 向量化 → Milvus 混合检索 → 候选+分数
#     → ③ _item_name_score_align：按分数分层 → confirmed(直接确认) / options(问用户)
#     → ④ _item_name_score_filter：confirmed 多于 1 个时，剔除与最高分差距 >0.15 的误判
#     → ⑤ Node._decide 决策写入 state：
#         有 confirmed  → state['item_names'] + ['rewritten_query']，下游走三路检索
#         仅有 options  → state['answer'] = 反问用户（answer 非空即"拦截"，不再检索）
#         两者皆空      → state['answer'] = 无法识别
#
# 三个类分工：Extractor 问 LLM，Aligner 问向量库，Node 负责串联与最终决策
# ============================================================================


class ItemNameExtractor:
  # LLM 提取器：多轮对话中用户常说"它/这个"，单看当前问题提取不出商品名，
  # 需把历史对话一并交给 LLM；任何环节失败都回退兜底结果（空列表 + 原始问题），保证流程不中断
  def __init__(self, logger, node_name):
    self.logger = logger
    self.node_name = node_name

  def extract_item_name(self, original_query: str, history_context: str) -> Dict[str, Any]:
    """LLM提取商品名称及问题重写
      original_query: "RS-12怎么测量电阻"       原始问题
      history_context: 历史对话上下文信息
      return {
        "item_names": ["RS-12", "RS-12 万用表"],
        "rewritten_query": "RS-12 万用表怎么测量电阻"
      }
    """
    # 1. 初始化兜底结果：商品名置空、问题回退为原始问题，保证解析失败时下游仍可用
    result = {"item_names": [], "rewritten_query": original_query}

    # 2. 获取 LLM 客户端；失败则直接返回兜底结果（降级不中断流程）
    llm_client = AIClients.get_llm_openai(response_format=False)
    if llm_client is None:
      self.logger.warning("LLM客户端初始化失败！")
      return result  # 降级处理

    # 3. 准备提示词：系统角色设定 + 注入原始问题与历史上下文的用户模板
    system_prompt = "你是一个专业的客服助手，擅长理解用户意图和提取关键信息。"
    human_prompt = ITEM_NAME_EXTRACT_TEMPLATE.format(query=original_query, history_text=history_context)

    # 4. 调用 LLM，基于历史对话提取商品名并重写问题
    llm_response = llm_client.invoke(
      [
        SystemMessage(content=system_prompt),
        HumanMessage(content=human_prompt)
      ]
    )

    llm_content = llm_response.content.strip()
    # parsed_llm_content = self._clean_parse(llm_content)

    # 5. 清洗解析 JSON 输出并回填结果；失败仅记录日志、沿用兜底结果（不中断流程）
    try:
      parsed_result = self._clean_parse(llm_content)
      result["rewritten_query"] = parsed_result.get("rewritten_query", original_query)
      result["item_names"] = parsed_result.get("item_names")
    except Exception as e:
      self.logger.error(f"清洗以及解析LLM的输出失败: {str(e)}")
    # llm_response_json = json.loads(llm_content)
    return result

  def _clean_parse(self, llm_content: str) -> Dict[str, Any]:
    """清洗并解析 LLM 响应"""
    # 1. 剥离 LLM 输出外层可能包裹的 Markdown 代码围栏（```json ... ```），得到纯 JSON 文本
    cleaned = re.sub(r"^```(?:json)?\s*", "", llm_content.strip())
    content = re.sub(r"\s*```$", "", cleaned)

    try:
      # 2. JSON 反序列化为字典
      parsed_llm_result: Dict[str, Any] = json.loads(content)

      # 3. 清洗商品名列表：类型非 list 视为无效置空；再过滤列表中的空白项
      raw_item_names = parsed_llm_result.get('item_names')
      if not isinstance(raw_item_names, list):
        clean_item_names = []
      else:
        clean_item_names = [raw_item for raw_item in raw_item_names if raw_item.strip()]

      # 4. 清洗重写问题：类型非 str 视为无效置空；str 则仅去除首尾空白
      raw_rewritten_query = parsed_llm_result.get('rewritten_query')
      clean_rewritten_query = "" if not isinstance(raw_rewritten_query, str) else raw_rewritten_query.strip()

      # 5. 返回结构化清洗结果
      return {"item_names": clean_item_names, "rewritten_query": clean_rewritten_query}
    except JSONDecodeError as e:
      # 6. LLM 输出非合法 JSON 时转为显式异常抛出，由上层决定降级策略
      raise ValueError(f"JSON反序列LLM的输出失败：{str(e)}")


class ItemNameAligner:
  """
  商品名对齐器
  主要职责：
  1. 查询匹配向量数据库
  2. 评分对齐
  3. 分数过滤
  """

  def __init__(self, logger, node_name):
    self.logger = logger
    self.node_name = node_name

  # 向量匹配 评分对齐 分数过滤
  # 对齐器主入口，三步串行流水线；返回 (confirmed 确认列表, options 候选列表)
  def match_align_filter(self, item_names: List[str],item_name_collection: str) -> Tuple[List[str], List[str]]:
    # confirmed = []
    # options = []
    # 向量化
    search_result: List[Dict[str, Any]] = self._match_vector(item_names,item_name_collection)
    print("search_result:",search_result)

    # 评分对齐
    confirmed, options = self._item_name_score_align(search_result)

    # 分数过滤
    if len(confirmed) > 1:
      confirmed = self._item_name_score_filter(confirmed, search_result)

    return confirmed, options


  def _match_vector(self, item_names: List[str],item_name_collection: str) -> List[Dict[str, Any]]:
    """
    向量匹配
      将商品名称向量化
      创建查询请求
      执行混合搜索
      获取搜索结果
      item_names:LLM提取到的商品名称列表
      return [
        {
          "extracted_name": "RS-12数字万用表",
          "matches": [
            {"item_name": "RS-12数字万用表", "score": 0.9},
            {"item_name": "RS-13数字万用表", "score": 0.69}
          ]
        },
        {
          "extracted_name": "HAK180",
          "matches": [
            {"item_name": "HAK180", "score": 0.75},
            {"item_name": "HAK181", "score": 0.6}
          ]
        }
      ]
    """
    result:List[Dict[str, Any]] = []

    # 将商品名称向量化  
    bge_m3_client = AIClients.get_bge_m3_client()
    if bge_m3_client is None:
      self.logger.warning("BGE-M3客户端初始化失败，无法进行向量匹配")
      return result

    milvus_client = StorageClients.get_milvus_client()
    if milvus_client is None:
      self.logger.warning("Milvus客户端初始化失败，无法进行向量匹配")
      return result
    
    # 将商品名称向量化 用于混合检索的条件
    # 一次性批量向量化所有提取名，下面逐个执行检索（每个提取名 = 一次混合搜索）
    embedding_result = generate_bge_m3_hybrid_vectors(bge_m3_client, item_names)

    for index,extract_item_name in enumerate(item_names):
      # 创建查询请求 [稠密查询请求，稀疏查询请求]
      hybrid_search_requests: List[AnnSearchRequest] = create_hybrid_search_requests(
        dense_vector=embedding_result['dense'][index],
        sparse_vector=embedding_result['sparse'][index],
      )
      # 执行混合搜索
      hybrid_search_result = execute_hybrid_search_query(
        milvus_client,
        collection_name=item_name_collection,
        search_requests=hybrid_search_requests,
        ranker_weights=(0.5, 0.5),  
        norm_score=True,  #RRF倒序融合排序 需要将IP查询得分进行归一化处理
        output_fields=["item_name"]
      )
      result.append({
        "extracted_name": extract_item_name,  # LLM提取的商品名称
        "matches": [
          {"item_name":h["entity"]["item_name"],"score":h["distance"]}
          for h in hybrid_search_result[0] if hybrid_search_requests
        ]
      })
    # 获取搜索结果
    return result


  def _item_name_score_align(self, search_results: List[Dict[str, Any]]) -> Tuple[List[str], List[str]]:
    """
    评分对齐
    search_results: List[Dict[str, Any]]  搜索结果列表
    [
      {
        "extracted_name": "RS-12数字万用表",
        "matches": [
          {"item_name": "RS-12数字万用表", "score": 0.9},
          {"item_name": "RS-13数字万用表", "score": 0.69}
        ]
      },
      {
        "extracted_name": "HAK180",
        "matches": [
          {"item_name": "HAK180", "score": 0.75},
          {"item_name": "HAK181", "score": 0.6}
        ]
      }
    ]

    return
    tuple[confirmed:List[str],options:List[str]]
    confirmed: 确认的商品名列表，传给下游多路检索
    options: 候选商品名列表，用于询问用户
    """
    confirmed:List[str] = []
    options:List[str] = []
    for search_result in search_results:
      extracted_name = search_result.get("extracted_name")
      sorted_matches = sorted(search_result.get("matches"), key=lambda x: x['score'], reverse=True) # 按分数降序排序
      # 获取大于0.7元素  高置信
      high = [match for match in sorted_matches if match.get('score') >= 0.7]
      if high:
        # 处理高置信
        # 场景1 数据库中匹配的商品名称如果与LLM提取的名称一致，直接确认
        # next(iterable, default) —— 取第一个元素；如果一个都没找到，就返回第二个参数给的默认值（None）
        extract = next((h for h in high if h["item_name"] == extracted_name), None)
        if extract:
          picked = extract["item_name"]
          if picked not in confirmed:
            confirmed.append(picked)
        # 场景2：唯一高分且与提取名不完全一致 → 分数足够高(≥0.85)才视为同一商品的不同写法直接确认；
        # 0.7~0.85 区间的归一化排名分存在虚高误匹配风险，降级走场景3 反问用户
        elif len(high) == 1 and high[0]["score"] >= 0.85:
          picked = high[0]["item_name"]
          if picked not in confirmed:
            confirmed.append(picked)
        # 场景3：多个高置信候选且无精确同名（或唯一高分置信不足）→ 无法替用户做主，放入 options 反问
        else:
          for h in high[:3]:
            picked = h["item_name"]
            if picked not in options and picked not in confirmed:
              options.append(picked)
      else:
        # 处理中置信（0.6~0.7：达不到确认标准，只作为候选让用户选择）
        mid = [match for match in sorted_matches
          if match['score'] >= 0.6 
          and match.get('item_name') not in options 
          and match.get('item_name') not in confirmed]
        if mid:
          for m in mid[:3]:
            picked = m["item_name"]
            options.append(picked)

    # options 最多返回 3 个，避免反问用户时选项过多
    return confirmed, options[:3]


  def _item_name_score_filter(self, confirmed: List[str], search_results: List[Dict[str, Any]]):
    """分数差异过滤，剔除误判"""
    # 为什么需要：用户一句话可能提到多个商品，但库里未必都有；
    # 若某个 confirmed 的分数远低于其他（如 0.9 vs 0.4），大概率是向量检索"硬凑"出来的误匹配
    # 1. 收集每个已确认商品名在所有搜索结果中的最高分
    item_name_score = {}
    for search_result in search_results:
      matches = search_result.get('matches')
      for m in matches:
        score = m.get('score')
        item_name = m.get('item_name')
        if item_name in confirmed:
          item_name_score[item_name] = max(item_name_score.get(item_name) or 0, score)

    # 2. 按最高分降序，取全场最高分作为基准
    sorted_item_name_score = sorted(item_name_score.items(), key=lambda x: x[1], reverse=True)
    max_item_name_score = sorted_item_name_score[0][1]

    # 3. 与最高分差距超过 0.15 的商品名视为误判，剔除
    return [name for name, score in item_name_score.items() if max_item_name_score - score <= 0.15]



class ItemNameConfirmNode(BaseNode):
  # 商品名称确认节点：结合历史对话提取商品名并重写问题，决定后续检索走向
  # 输入 state：session_id、original_query
  # 输出 state：item_names(确认商品)、rewritten_query(改写问题)、
  #             answer(拦截话术，非空则主图路由直接结束、不再检索)、history(历史对话)
  name = "item_name_confirm"

  def __init__(self):
    # logger / name 由 BaseNode 基类提供；提取器复用节点的日志与名称配置
    super().__init__()
    self.item_name_extractor = ItemNameExtractor(self.logger, self.name)
    self.item_name_aligner = ItemNameAligner(self.logger, self.name)

  def process(self, state: QueryGraphState) -> QueryGraphState:
    # 1.获取历史会话：拉取最近 10 条（5 轮）对话，拼接为 "role:text" 格式的上下文文本
    session_id = state.get("session_id")
    original_query = state.get("original_query")

    history_messages: List[Dict[str, Any]] = get_recent_messages(session_id)
    # 逐条拼接历史对话；仅在无历史时使用占位文案（避免占位文案与真实历史拼成矛盾文本干扰 LLM）
    history_context = "\n".join(
      f"{message['role']}:{message['text']}" for message in history_messages
    ) or "暂无历史对话信息"
    print("10条数据（最近5轮对话）history_context:", history_context)

    # 2.LLM提取商品名称（返回 item_names 商品名列表 + rewritten_query 重写后问题，供步骤 3-5 使用）
    extractor_item_name: Dict[str, Any] = self.item_name_extractor.extract_item_name(original_query, history_context)
    item_names = extractor_item_name["item_names"]
    rewritten_query = extractor_item_name.get('rewritten_query')


    # 3.向量匹配 评分对齐  分数过滤
    confirmed: List[str] = []
    options: List[str] = []
    # 提取名列表为空（LLM 兜底结果）则跳过对齐，confirmed/options 保持为空 → 最终走"无法识别"分支
    if item_names:
      confirmed, options = self.item_name_aligner.match_align_filter(item_names,self.config.item_name_collection)

    # 4.决策分支 无答案->三路检索   有答案
    self._decide(state, item_names, confirmed, options, rewritten_query)

    # 6. 将历史对话写入 state，供下游使用（确认商品名已由 _decide 写入 state['item_names']）
    state["history"] = history_messages
    return state

  def _decide(self, state: QueryGraphState, item_names: List[str],confirmed: List[str], options: List[str], rewritten_query: str):
    """根据对齐结果更新 state"""
    # 分支1：有确认商品 → 写入检索要素，主图条件边据此路由到"三路检索"
    if confirmed:
      state['rewritten_query'] = rewritten_query
      state['item_names'] = confirmed
    # 分支2：只有候选 → answer 非空即"拦截"，直接反问用户，不再检索
    elif options:
      state['answer'] = (
        f"我不确定您指的是哪款产品。"
        f"您是在询问以下产品吗：{'、'.join(options)}？"
      )
    # 分支3：确认与候选都为空 → 直接告知无法识别（同样走拦截）
    else:
      state['answer'] = "抱歉，我无法识别您询问的具体产品名称，请提供更准确的产品名称或型号。"


if __name__ == '__main__':
  item_name_confirmed_node = ItemNameConfirmNode()
  init_state = {
    "session_id": "123456",
    "original_query": "RS-12数字万用表和H3C LA2608 室内无线网关的操作区别是什么?"
    # "original_query": "RS-12数字万用表和RS-13数字万用表的区别?"
    # "original_query": "RS-12数字万用表如何测量电压以及HAK180的介质规格有哪些?"
    # "original_query": "RS-12数字万用表如何测量电压"  # 单个商品询问
    # "original_query": "今天天气怎么样？"
  }
  llm_result = item_name_confirmed_node(init_state)

  print(llm_result)

