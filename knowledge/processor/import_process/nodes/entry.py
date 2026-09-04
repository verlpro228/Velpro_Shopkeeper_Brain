
import json
from pathlib import Path

from knowledge.processor.import_process.base import BaseNode, setup_logging
from knowledge.processor.import_process.exceptions import ValidationError
from knowledge.processor.import_process.state import ImportGraphState


class EntryNode(BaseNode):
    # 入口节点：根据上传文件的后缀决定走哪个分支
    name = "entry_node"
    # 实现父类中继承的方法 每个子节点都需要处理自己的那一步任务 任务总体日志 任务执行时间等都由父类方法来管理
    def process(self, state: ImportGraphState) -> ImportGraphState:
        self.log_step("step1","获取文件或目录")
        # 1.获取上传文件以及路径
        import_file_path = state.get("import_file_path","")
        file_dir = state.get("file_dir","")
        self.log_step("step2","校验文件是否存在")
        # 2.判断文件是否为空
        if not import_file_path:
            raise ValidationError(f"import_file_path is empty!",self.name)

        # 3.获取文件后缀 转化小写
        path = Path(import_file_path)
        if not path.exists():
            raise ValidationError(f"file {path.name} not exists!",self.name)
        suffix = path.suffix.lower()
        # 4.不同文件类型做不同状态的处理
        self.log_step("step3","判断文件类型")
        if suffix == ".pdf":
            state["is_pdf_read_enabled"] = True
            state["pdf_path"] = str(path.parent)
        elif suffix == ".md":
            state["is_md_read_enabled"] = True
            state["md_path"] = import_file_path
        else:
            raise ValidationError(f"file {path.name} -> type {suffix} is not supported!",self.name)
        # 5.获取文件标题 不带扩展名
        file_name = path.stem


        # 6.更新状态并返回状态
        state["file_title"] = file_name
        return state

if __name__ == "__main__":
    setup_logging()
    import_file_path = r"C:\Users\14207\Desktop\doc\万用表RS-12的使用.pdf"
    file_dir = r"C:\Users\14207\Desktop\doc\temp_dir"
    state = {
        "is_pdf_read_enabled":False,
        "is_md_read_enabled":False,
        "import_file_path":import_file_path,
        "file_dir":file_dir,
    }
    entry = EntryNode()
    processed_state = entry(state)
    print(json.dumps(processed_state,ensure_ascii=False,indent=4))
