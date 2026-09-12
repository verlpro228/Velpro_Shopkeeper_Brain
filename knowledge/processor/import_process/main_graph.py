"""
  创建导入流程图
  :return: 编译后的StateGraph实例
  流程结构：
      entry_node
            │
            ├── (PDF) ──> pdf_to_md_node ──┐
            │                              │
            └── (MD) ─────────────────────>├──> md_img_node
                                            │
                                            v
                                    document_split_node
                                            │
                                            v
                                    item_name_rec_node
                                            │
                                            v
                                      bge_embedding_node
                                            │
                                            v
                                      import_milvus_node
                                            │
                                            v
                                            END
"""

import json
from langgraph.constants import END
from langgraph.graph.state import CompiledStateGraph, StateGraph
from knowledge.processor.import_process.base import setup_logging
from knowledge.processor.import_process.nodes.pdf_to_md import PdfToMdNode
from knowledge.processor.import_process.nodes.md_img import MdImgNode
from knowledge.processor.import_process.nodes.ducment_split import DocumentSplitNode
from knowledge.processor.import_process.nodes.entry import EntryNode
from knowledge.processor.import_process.nodes.item_name_recognition import ItemNameRecognitionNode
from knowledge.processor.import_process.nodes.bge_embedding import BgeEmbeddingChunksNode
from knowledge.processor.import_process.nodes.import_milvus import ImportMilvusNode
from knowledge.processor.import_process.state import ImportGraphState,create_default_state

 
def import_router(state:ImportGraphState) -> str:
  """
  入口节点的路由逻辑
  根据文件类型决定走pdf分支还是直接处理md文档
  :param state: 导入图的状态
  :return: 路由后的节点名称
  """
  if state.get("is_md_read_enabled"):
    return "md_img_node"
  if state.get("is_pdf_read_enabled"):
    return "pdf_to_md_node"
  return END




def create_import_graph() -> CompiledStateGraph:
  """
    创建导入流程图
    :return: 编译后的StateGraph实例
  """
  # 1.创建
  graph = StateGraph(ImportGraphState)

  # 2.定义节点
  nodes = {
    "entry_node": EntryNode(),
    "pdf_to_md_node": PdfToMdNode(),
    "md_img_node": MdImgNode(),
    "document_split_node": DocumentSplitNode(),
    "item_name_rec_node": ItemNameRecognitionNode(),
    "bge_embedding_node": BgeEmbeddingChunksNode(),
    "import_milvus_node": ImportMilvusNode()
  }

  # 2.1设置开启节点
  graph.set_entry_point("entry_node")

  # 2.2添加节点到所有图中
  for key,value in nodes.items():
    graph.add_node(key,value)

  # 3. 定义边（条件边，顺序边）

  # 3.1 条件边： 入口节点后根据文件类型路由
  # 定义路由映射表（routing map），将路由函数的返回值映射到实际的下一个节点。
  # 键（Key）：import_router 函数可能返回的值
  # 值（Value）：实际要跳转到的目标节点名称
  graph.add_conditional_edges(
    "entry_node",  # ① 起点：从哪个节点出来时触发判断
    import_router, # ② 判断函数（注意：没有括号，传的是函数本身）
    {    # ③ 路由表：判断结果 → 实际去向
      "md_img_node":"md_img_node",
      "pdf_to_md_node":"pdf_to_md_node",
      END:END
    }
  )
  graph.add_edge("pdf_to_md_node","md_img_node")
  graph.add_edge("md_img_node","document_split_node")
  graph.add_edge("document_split_node","item_name_rec_node")
  graph.add_edge("item_name_rec_node","bge_embedding_node")
  graph.add_edge("bge_embedding_node","import_milvus_node")
  graph.add_edge("import_milvus_node",END)


  # 4. 编译
  return graph.compile()
# 提前把整条导入流水线组装好，存到一个全局变量里。
kb_import_graph_app = create_import_graph()

def run_import_graph(import_file_path:str,file_dir:str)->dict:
  """
    便捷函数：运行导入流程
    :param import_file_path:
    :param file_dir:
    :return: 最终状态字典
  """
  state = {
    "import_file_path":import_file_path,
    "file_dir":file_dir,
  }
  # ** state 是 Python 的解包操作，将字典展开为关键字参数
  # 相当于：create_default_state(import_file_path=..., file_dir=...)
  # "is_pdf_read_enabled": False,  # 默认值
  # "is_md_read_enabled": False,  # 默认值
  # "md_path": None,  # 默认值  
  init_state = create_default_state(**state)
  final_state = None
  # 启动  LangGraph 工作流并流式执行（streaming execution）。
  # kb_import_graph_app: 已编译的 LangGraph 应用对象
  # .stream(state): 以流式方式执行工作流，每完成一个节点就 yield 一次结果
  # event: 每次迭代返回的事件数据，包含当前节点的名称和更新后的状态
  # 优势：可以实时看到每个节点的执行进度，而不是等待全部完成
  for event in kb_import_graph_app.stream(state):
    for node_name,state in event.items():
      print(f"节点: {node_name} 执行状态: {state}")
      final_state = state  # 循环结束后，final_state 保存的是最后一个节点执行后的状态
  return final_state  # 返回工作流执行完成后的最终状态。

if __name__ == "__main__":
  setup_logging()
  
  import_file_path = r"C:\Users\14207\Desktop\doc\万用表RS-12的使用.pdf"
  file_dir = r"C:\Users\14207\Desktop\doc\temp_dir"
  final_state = run_import_graph(import_file_path,file_dir)
  # indent=2 — 美化输出，每层缩进 2 个空格。
  # ensure_ascii=False — 允许直接输出中文。
  # json.dumps() 函数默认将中文转换为 ASCII 编码，
  # 而 ensure_ascii=False 则可以保持中文的原始编码。
  print(json.dumps(final_state,indent=2,ensure_ascii=False))
  print("-" * 50)
  # get_graph() —— 从编译好的图里取出图的结构信息
  # print_ascii() —— 用 ─ │ ┌ ┐ ▼ 这类
  # ASCII
  # 字符画出节点和箭头
  kb_import_graph_app.get_graph().print_ascii()
