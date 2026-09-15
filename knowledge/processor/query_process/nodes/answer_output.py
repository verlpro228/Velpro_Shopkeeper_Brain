from knowledge.processor.query_process.base import BaseNode
from knowledge.processor.query_process.state import QueryGraphState


class AnswerOutputNode(BaseNode):
  # 答案输出节点
  name = "answer_output"

  def process(self, state: QueryGraphState) -> QueryGraphState:
    return state
