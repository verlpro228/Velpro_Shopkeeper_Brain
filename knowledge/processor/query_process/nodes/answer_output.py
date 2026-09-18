from typing import List, Dict, Tuple

from knowledge.processor.query_process.base import BaseNode
from knowledge.processor.query_process.state import QueryGraphState
from knowledge.prompt.query_prompt import ANSWER_PROMPT
from knowledge.utils.client.ai_clients import AIClients
from knowledge.utils.mongo_history_util import save_chat_message
from knowledge.utils.sse_util import push_sse_event, SSEEvent
from knowledge.utils.task_util import set_task_result


class AnswerOutputNode(BaseNode):
  # 答案输出节点
  name = "answer_output"

  def process(self, state: QueryGraphState) -> QueryGraphState:
    session_id = state.get("session_id")
    task_id = state.get("task_id")

    # 检查是否有答案
    if state.get("answer"):
      # 1.1 有答案：在商品名称确认节点中，生成答案：[用户选择 or 抱歉]，不进行三路检索，直接生成答案
      self._push_existing_answer(state, task_id)  # 有答案 并且非流式输出
    else:
      # 1.2 无答案：进行三路检索，排序，再生成答案，输出答案
      prompt = self._build_prompt(state)
      self._generate_answer(state, prompt)

    self._write_history(state)

    if state.get("is_stream"):
      # 流式输出 无论有无答案，最终都需要输出终止事件状态，即通知客户端关闭SSE连接
      # 有答案 流式输出
      # 无答案 流式输出
      push_sse_event(task_id=task_id, event=SSEEvent.FINAL, data={"answer": state.get("answer")})

    return state

  # ================================================================== #
  #                    已有答案推送                                      #
  # ================================================================== #

  # 处理已有答案 并且非流式输出
  def _push_existing_answer(self, state, task_id):
    # 已有答案且非流式输出时，直接把已生成的答案写入任务结果，供轮询接口读取
    # 注意 task_id 是字符串，直接透传，不能 task_id["task_id"] 下标取值
    if not state.get("is_stream"):
      set_task_result(task_id, "answer", state["answer"])

  # ================================================================== #
  #                    提示词构建                                        #
  # ================================================================== #

  def _build_prompt(self, state: QueryGraphState) -> str:
    """根据检索结果、历史对话组装 LLM 提示词（预算分配的总指挥）"""
    # char_budget：检索文档 + 历史对话共享同一份预算，靠“返回剩余预算”接力传递，
    # 保证总长度不超过 max_context_chars，避免单侧撑爆上下文
    char_budget = self.config.max_context_chars

    # 1. 取问题：优先重写后的(挤掉语气词/指代更规范)，没有就用原文兜底
    question = state.get("rewritten_query") or state.get("original_query", "")
    item_names = state["item_names"]  # 商品名必须存在，依赖上游节点填好

    # 2. 先做文档：相关上下文信息量最大、优先级最高，优先占用预算
    #    返回值里覆盖 char_budget 即看作把“剩余额度”传下去（接力点）
    context_str, char_budget = self._format_reranked_docs(state.get("reranked_docs") or [], char_budget)

    # 3. 再做历史：用文档剩下的预算，保证文档绝不因历史被挤掉
    history_str, char_budget = self._format_chat_history(state.get("history") or [], char_budget)

    # 5. 组装提示词：四个字段都给兜底文案，避免模板里露出空串
    return ANSWER_PROMPT.format(
      context=context_str or "无参考内容",
      history=history_str if history_str else "暂无历史对话",
      item_names=", ".join(item_names),
      question=question,
    )

  def _format_reranked_docs(self, reranked_docs: List[Dict], char_budget: int) -> Tuple[str, int]:
    """把重排结果转成"带标签的引用块"，受字符预算约束"""
    formatted_lines = []  # 收集每一篇成型条目
    used_chars = 0  # 累计已用字符

    for idx, doc in enumerate(reranked_docs, 1):  # 从 1 开始编号，给 LLM 引用序号
      content = doc.get("content", "").strip()
      if not content:
        continue  # 空正文跳过；但序号不重排(按原文档位置计)，保证后续引用编号稳定

      # 组装"元信息标签"，给每篇文档贴上可溯源的身份
      meta_tags = [f"[{idx}]"]  # 0 引用序号
      for field, template in [
        ("source", "[source={}]"),  # 来源：local / web，供模型区分知识库或网页
        ("chunk_id", "[chunk_id={}]"),  # 本地切片的唯一 id
        ("url", "[url={}]"),  # web 来源的链接
        ("title", "[title={}]"),  # 标题
      ]:
        field_value = str(doc.get(field, "")).strip()
        if field_value:
          meta_tags.append(template.format(field_value))
      #   为什么有值才加？空标签是噪声，既浪费 token 又干扰模型；缺省不硬造。

      relevance_score = doc.get("score")
      if relevance_score is not None:
        meta_tags.append(f"[score={float(relevance_score):.4f}]")
      #   score 用 .4f 固定 4 位：避免 0.111111111... 长尾占 token，也让模型好横向比较可信度

      doc_entry = " ".join(meta_tags) + "\n" + content  # 标签行 + 空行 + 正文

      # 预算检查：装不下就“整条”丢弃，绝不塞半篇正文，防止 token 截断烂尾
      if used_chars + len(doc_entry) > char_budget:
        break

      formatted_lines.append(doc_entry)
      used_chars += len(doc_entry) + 2  # +2 为后续 "\n\n" 分隔符预留字符，保证严格不超预算

    # 返回拼接好的文本 + 剩余预算(接力传给历史对话使用)
    return "\n\n".join(formatted_lines), char_budget - used_chars

  def _format_chat_history(self, chat_history: List[Dict], char_budget: int) -> Tuple[str, int]:
    """把多轮历史对话压成"用户/助手"文本，受字符预算约束"""
    formatted_lines = []  # 收集每一行成型条目
    used_chars = 0  # 累计已用字符

    # 把英文 role 映射成中文昵称，让模板里的对话读起来自然
    role_label_map = {"user": "用户", "assistant": "助手"}

    # 按时间先后逐条处理(传入顺序通常已是从早到晚)
    for message in chat_history:
      role = message.get("role", "")
      text = message.get("text", "")
      # 跳过空内容 / system 等非 user/assistant 角色，避免非对话消息混入给模型看的话
      if not text or role not in role_label_map:
        continue

      formatted_line = f"{role_label_map[role]}: {text}"

      # 预算检查：放不下就“整条”终止；保留更早轮次、丢弃更晚的——
      # 早期往往是背景/前提，越晚越贴近当前提问，但预算不足时保前保底
      if used_chars + len(formatted_line) > char_budget:
        break

      formatted_lines.append(formatted_line)
      used_chars += len(formatted_line) + 1  # +1 为后续 "\n" 分隔符预留字符

    return "\n".join(formatted_lines), char_budget - used_chars

    # 无答案 -》 三路检索 -》 生成答案

  # ================================================================== #
  #                    LLM 生成                                         #
  # ================================================================== #

  def _generate_answer(self, state, prompt):
    """调用 LLM 生成答案（流式/非流式）"""
    self.log_step("generate", "生成答案")
    # get_llm_openai(False)：获取"无 JSON 格式约束"的文本客户端，
    # 因为这里是自由文本答案，不需要强制 JSON 结构化输出
    llm_client = AIClients.get_llm_openai(False)
    if llm_client is None:
      raise ValueError("LLM 客户端初始化失败")

    task_id = state["task_id"]

    # 按是否流式分流：
    #   - 流式：逐 chunk 推送 delta 事件，客户端边收边渲染；最终答案由 process 统一推送 FINAL
    #   - 非流式：一次拿到完整答案，并已写入任务结果供轮询读取
    if state.get("is_stream"):
      state["answer"] = self._stream_generate(llm_client, prompt, task_id)
    else:
      state["answer"] = self._invoke_generate(llm_client, prompt)
      set_task_result(task_id, "answer", state["answer"])

  def _invoke_generate(self, llm_client, prompt: str) -> str:
    """非流式生成"""
    self.log_step("generate", "生成答案")

    try:
      response = llm_client.invoke(prompt)
      return response.content
    except Exception as e:
      # 降级：LLM 调用失败也要返回可读文案，不让下游因空答案而崩
      self.logger.error(f"生成回答出错: {e}")
      return "抱歉，生成回答时出现错误。"

  def _stream_generate(self, llm_client, prompt, task_id):
    """流式生成，逐 chunk 推送 delta 事件"""
    accumulated_answer = ""  # 边推边攒，最后收集完整回答回填 state
    try:
      for chunk in llm_client.stream(prompt):  # 逐 token/chunk 产出
        # getattr 兼容不同 chunk 结构；无 content 字段或空串则跳过该 chunk
        delta_text = getattr(chunk, "content", "") or ""
        if delta_text:
          accumulated_answer += delta_text  # 累计完整答案
          push_sse_event(task_id, SSEEvent.DELTA, {"delta": delta_text})  # 推送增量给客户端实时渲染（用常量避免魔法字符串写错事件名）
    except Exception as e:
      self.logger.error(f"流式生成出错: {e}")
    return accumulated_answer

  # ================================================================== #
  #                    历史记录                                         #
  # ================================================================== #

  def _write_history(self, state: QueryGraphState):
    """将用户问题和助手回答写入 MongoDB 历史记录"""
    session_id = state["session_id"]
    # 记录用重写后的规范化问题，便于后续检索/复盘；无重写则退回原文
    rewritten_query = state.get("rewritten_query", "") or state.get("original_query", "")
    item_names = state.get("item_names") or []  # 商品名可能没有，兜底为空列表

    try:
      # 1. 写用户问题：用原始提问原文存档(保留用户真实表述)
      save_chat_message(
        session_id=session_id,
        role="user",
        text=state["original_query"],
        rewritten_query=rewritten_query,
        item_names=item_names,
      )
      # 2. 写助手回复：仅当确有答案才入库，避免写入空回复
      if state.get("answer"):
        save_chat_message(
          session_id=session_id,
          role="assistant",
          text=state["answer"],
          rewritten_query=rewritten_query,
          item_names=item_names,
        )
    except Exception as e:
      # 历史记录是旁路能力，写失败只告警，不阻断主流程返回
      self.logger.warning(f"写入历史记录失败: {e}")


if __name__ == "__main__":
  from dotenv import load_dotenv
  import json

  load_dotenv()

  from knowledge.processor.query_process.base import setup_logging

  setup_logging()

  print("=" * 60)
  print("开始测试: 答案生成节点 (AnswerOutputNode)")
  print("=" * 60)

  # 构造模拟状态
  mock_state = {
    "task_id": "test_task_001",
    "session_id": "test_session_001",
    "is_stream": False,
    "original_query": "万用表怎么测电压？",
    "rewritten_query": "RS-12数字万用表如何测量电压？",
    "item_names": ["RS-12数字万用表"],
    "reranked_docs": [
      {
        "content": "数字万用表测量电压步骤：1. 将旋钮转到V档位；2. 黑表笔插COM孔，红表笔插V孔；3. 将表笔并联到被测点两端。",
        "source": "local",
        "chunk_id": "chunk_001",
        "title": "万用表使用手册",
        "score": 0.9234
      },
      {
        "content": "测量直流电压时需注意正负极性，红表笔接正极，黑表笔接负极。",
        "source": "web",
        "url": "https://example.com/guide",
        "title": "电压测量指南",
        "score": 0.8756
      }
    ],
    "history": [
      {"role": "user", "text": "万用表是什么？"},
      {"role": "assistant", "text": "万用表是一种多功能电子测量仪器..."}
    ],
  }

  print("【输入状态】:")
  print(f"  query: {mock_state['rewritten_query']}")
  print(f"  item_names: {mock_state['item_names']}")
  print(f"  reranked_docs: {len(mock_state['reranked_docs'])} 篇")
  print("-" * 60)

  # 执行答案生成
  node = AnswerOutputNode()
  result = node.process(mock_state)

  # 打印结果
  print("\n【生成结果】:")
  print("-" * 60)
  print(result.get("answer", "无答案"))
  print("-" * 60)

  print("\n测试完成")
