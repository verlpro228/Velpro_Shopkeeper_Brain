from typing import Tuple
import subprocess
from pathlib import Path
import json
from knowledge.processor.import_process.base import BaseNode, T, setup_logging
from knowledge.processor.import_process.exceptions import ValidationError, FileProcessingError, PdfConversionError
from knowledge.processor.import_process.state import ImportGraphState


class PdfToMdNode(BaseNode):
  #   使用minerU将pdf转换为md格式
  name = "pdf_to_md_node"

  def process(self, state: ImportGraphState) -> ImportGraphState:
    # 参数校验
    import_file_path_obj,file_dir_path_obj = self._validate_state_inputs_path(state)

    # 用MinerU将pdf转换为md格式
    processed_code = self._execute_mineru(import_file_path_obj,file_dir_path_obj)
    if processed_code == 0:
      self.logger.info(f'MinerU转换PDF为MD成功!!!{import_file_path_obj}-输出{file_dir_path_obj}')
    else:
      self.logger.error(f'MinerU转换PDF为MD失败!!!{import_file_path_obj}-输出失败{file_dir_path_obj}')
      raise PdfConversionError(f'MinerU转换PDF为MD失败!!!{import_file_path_obj}-输出失败{file_dir_path_obj}',self.name)

    # 获取md文件路径（完整）
    md_path = self._get_md_paths(import_file_path_obj,file_dir_path_obj)

    # 修改状态并返回
    state["md_path"] = md_path


    return state
  
  
  def _validate_state_inputs_path(self, state: ImportGraphState) -> Tuple[Path,Path]:
    """
      校验状态输入参数路径是否存在
    """
    import_file_path = state.get("import_file_path","")
    file_dir = state.get("file_dir","")

    if not import_file_path:
      raise ValidationError("输入文件路径为空",self.name)

    import_file_path_obj = Path(import_file_path)

    if not import_file_path_obj.exists():
      raise FileProcessingError("输入文件路径不存在",self.name)
    if not file_dir:
      file_dir = import_file_path_obj.parent
    file_dir_path_obj = Path(file_dir)


    return import_file_path_obj,file_dir_path_obj


  def _execute_mineru(self, import_file_path_obj, file_dir_path_obj) -> int:
    """
      执行minerU转换
    """
    proc = subprocess.Popen(
      args=["mineru", "-p", import_file_path_obj, "-o", file_dir_path_obj, "--source", "local", "--backend", "pipeline"],
      stdout=subprocess.PIPE,    # 捕获标准输出
      stderr=subprocess.STDOUT,  # 合并错误到标准输出
      text=True,
      encoding="utf-8",
      errors="replace",          # 遇到乱码时替换
      bufsize=1                  # 行缓冲，实时输出
    )

    for line in proc.stdout:
      print(line.rstrip())   #rstrip() 去掉行尾换行符，避免打印出双倍空行。

    processed_code = proc.wait()  #wait() 阻塞直到子进程结束。返回的整数是退出码：0 = 成功，非 0 = 失败。

    return processed_code


  def _get_md_paths(self,import_file_path_obj:Path,file_dir_path_obj:Path) -> str:
    """
      获取md文件路径（完整）
    """

    file_name = import_file_path_obj.stem

    md_path = file_dir_path_obj/file_name/"auto"/f"{file_name}.md"
    return str(md_path)


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
    pdfToMdNode = PdfToMdNode()
    processed_state = pdfToMdNode(state)
    print(json.dumps(processed_state,ensure_ascii=False,indent=4))