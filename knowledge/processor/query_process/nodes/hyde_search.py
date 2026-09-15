from typing import Dict

from knowledge.processor.query_process.base import BaseNode
from knowledge.processor.query_process.state import QueryGraphState


class HyDeSearchNode(BaseNode):
  # 假设性文档答案检索
  name = "search_embedding_hyde"

  def process(self, state: QueryGraphState) -> Dict:
    return {}
