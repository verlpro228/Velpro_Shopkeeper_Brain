from typing import Dict

from knowledge.processor.query_process.base import BaseNode
from knowledge.processor.query_process.state import QueryGraphState


class VectorSearchNode(BaseNode):
  # 向量检索
  name = "search_embedding"

  def process(self, state: QueryGraphState) -> Dict:
    return {}
