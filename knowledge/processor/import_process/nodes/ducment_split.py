from knowledge.processor.import_process.base import BaseNode, T
from knowledge.processor.import_process.state import ImportGraphState


class DocumentSplitNode(BaseNode):
  name = "document_split_node"
  def process(self, state: ImportGraphState) -> ImportGraphState:
    return state
