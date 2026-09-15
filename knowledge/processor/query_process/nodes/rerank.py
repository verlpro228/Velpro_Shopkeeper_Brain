from knowledge.processor.query_process.base import BaseNode
from knowledge.processor.query_process.state import QueryGraphState


class RerankNode(BaseNode):
  # 重排序节点 精排序
  name = "rerank"

  def process(self, state: QueryGraphState) -> QueryGraphState:
    return state
