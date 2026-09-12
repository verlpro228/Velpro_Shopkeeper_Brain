from pymilvus import MilvusClient, DataType
from pymilvus.orm.schema import CollectionSchema
import logging
from knowledge.processor.import_process.base import BaseNode, setup_logging
from knowledge.processor.import_process.state import ImportGraphState
from knowledge.processor.import_process.exceptions import ValidationError
from knowledge.utils.client.storage_clients import StorageClients
from knowledge.processor.import_process.config import ImportConfig, get_config
from dataclasses import dataclass
from pathlib import Path
import json
from typing import Tuple, List, Dict, Any, Optional, Sequence

# 模块级命名 logger：__name__ 取本模块导入路径作 logger 名（同名全局唯一，导入时只建一次）
# 供无 self 的 @staticmethod 建造者使用（如 _MilvusSchemaBuilder.build）；节点实例方法则用 self.logger
logger = logging.getLogger(__name__)


# ================================================================== #
#                        标量字段规范                                   #
# ================================================================== #

# frozen=True：实例不可变（只读），作为模块级常量被多处复用时不怕被误改，且天然可哈希
@dataclass(frozen=True)
class ScalarFieldSpec:
  """单个标量字段的声明规范：字段名 + Milvus 类型 + 可选长度，供 Schema 建造者遍历建字段"""
  field_name: str
  datatype: DataType
  max_length: Optional[int] = None


# 模块级预定义的标量字段清单（复用）：建造者遍历它批量 add_field，
# 以后要加新标量字段只需在此追加一条，不必改动 Schema 构建逻辑
_SCALAR_FIELDS: Sequence[ScalarFieldSpec] = (
  ScalarFieldSpec(field_name="content", datatype=DataType.VARCHAR, max_length=65535),
  ScalarFieldSpec(field_name="title", datatype=DataType.VARCHAR, max_length=65535),
  ScalarFieldSpec(field_name="parent_title", datatype=DataType.VARCHAR, max_length=65535),
  ScalarFieldSpec(field_name="file_title", datatype=DataType.VARCHAR, max_length=65535),
  ScalarFieldSpec(field_name="item_name", datatype=DataType.VARCHAR, max_length=65535),
)


# ================================================================== #
#                        建造者：Schema 构建                           #
# ================================================================== #
class _MilvusSchemaBuilder:
  """职责：专门负责构建约束"""

  @staticmethod
  def build(client: MilvusClient, dim: int) -> CollectionSchema:
    logger.info("开始构建约束(schema)...")

    # enable_dynamic_field=True：开启动态字段——插入数据里没被 add_field 声明的 key 不会报错，
    schema = client.create_schema(enable_dynamic_field=True)

    # 2. 构建主键字段约束
    schema.add_field(
      field_name="chunk_id",
      datatype=DataType.INT64,
      is_primary=True,
      auto_id=True
    )

    # 3. 构建向量字段约束
    schema.add_field(
      field_name="dense_vector",
      datatype=DataType.FLOAT_VECTOR,
      dim=dim
    )
    schema.add_field(
      field_name="sparse_vector",
      datatype=DataType.SPARSE_FLOAT_VECTOR,
    )

    # 4. 构建标量字段约束
    for scalar_field in _SCALAR_FIELDS:
      kwargs: Dict[str, Any] = {
        "field_name": scalar_field.field_name,
        "datatype": scalar_field.datatype
      }
      if scalar_field.max_length is not None:
        kwargs['max_length'] = scalar_field.max_length
      schema.add_field(**kwargs)

    logger.info(f"构建约束(schema)完成...")
    return schema


# ================================================================== #
#                        建造者：索引构建                               #
# ================================================================== #

class _MilvusIndexBuilder:
  """职责：负责处理Milvus的索引"""

  @staticmethod
  def build(client: MilvusClient, collection_name: str):
    logger.info(f"开始构建集合 {collection_name} 索引...")

    index = client.prepare_index_params(collection_name=collection_name)

    # 稠密向量索引
    index.add_index(
      field_name="dense_vector",
      index_name="dense_vector_index",
      index_type="AUTOINDEX",
      metric_type="COSINE"
    )

    # 稀疏向量索引
    index.add_index(
      field_name="sparse_vector",
      index_name="sparse_vector_index",
      index_type="SPARSE_INVERTED_INDEX",
      metric_type="IP",
    )

    logger.info(f"构建集合 {collection_name} 索引完成...")
    return index


# ================================================================== #
#                        插入器：数据插入与回填                          #
# ================================================================== #

class _MilvusInserter:
  """职责：将数据插入到Milvus 以及 回填chunk_id"""

  def __init__(self, client: MilvusClient, collection_name: str):
    # 构造时绑定客户端与目标集合，后续 insert 无需重复传参；_ 前缀表私有，外部只经 insert 交互
    self._client = client
    self._collection_name = collection_name

  def insert(self, chunks: List[Dict[str, Any]]) -> List[dict[str, Any]]:
    logger.info(f"开始插入{len(chunks)}块到Milvus...")

    # 一次性批量插入：data 传 dict 列表，Milvus 按 key 名与 schema 字段对齐；
    # chunk 里未被 schema 声明的多余 key 不会报错（自动落进动态字段 $meta）
    inserted_result = self._client.insert(
      collection_name=self._collection_name,
      data=chunks
    )
    # 返回 dict：insert_count=服务端确认的成功条数；ids=主键列表——主键 auto_id 由服务端生成，客户端事前不知道
    inserted_count = inserted_result.get('insert_count')
    ids = inserted_result.get('ids')

    # 把服务端生成的主键写回内存中的 chunk，下游（备份 json / 展示 / 按 id 删除）才能引用
    self._fill_chunk_ids(chunks, ids)
    logger.info(f"完成插入{inserted_count}记录,并且回填chunk_id到chunk中")
    return chunks

  def _fill_chunk_ids(self, chunks: List[Dict[str, Any]], ids: List[Any]):
    # zip 按下标配对：Milvus 返回的 ids 顺序与 data 的插入顺序一一对应（官方保证），第 i 个 chunk 得第 i 个 id
    # chunk["chunk_id"] = id 是原地修改，调用方的 chunks 已带上主键，故本方法无需返回值
    for chunk, id in zip(chunks, ids):
      chunk["chunk_id"] = id


# ================================================================== #
#                        门面：主节点                                  #
# ================================================================== #

class ImportMilvusNode(BaseNode):
  """
  向量数据入库节点

  采用门面+建造者设计模式：
  - 门面角色：ImportMilvusNode 节点的 process()
  - 建造者：_MilvusSchemaBuilder, _MilvusIndexBuilder, _MilvusInserter
  """
  name = "import_milvus_node"

  def process(self, state: ImportGraphState) -> ImportGraphState:
    # 1. 参数校验
    validated_chunks, dim, config = self._validate_get_inputs(state)

    # 2. 获取milvus客户端
    milvus_client = StorageClients.get_milvus_client()

    if milvus_client is None:
      return state

    # 3. 获取集合名字
    collection = getattr(config, 'chunks_collection')

    # 4. 确保集合存在
    self._ensure_has_collection(milvus_client, collection, dim)

    # 5. 插入
    inserter = _MilvusInserter(client=milvus_client, collection_name=collection)
    final_chunks = inserter.insert(chunks=validated_chunks)

    # 6. 更新state
    state['chunks'] = final_chunks

    return state

  # 参数校验：过滤双向量齐全的 chunk，顺便收集向量维度供建集合 schema 用
  def _validate_get_inputs(self, state: ImportGraphState) -> Tuple[List, int, ImportConfig]:
    """参数校验"""
    self.log_step("step1", "参数校验")

    config = get_config()
    chunks = state.get('chunks')
    # 空 chunks 说明上游节点失败/没产出，无法入库
    if not chunks:
      raise ValidationError("待入库的切块chunk不存在", self.name)
    # 过滤残缺块：向量化失败的批没有向量字段，混进 insert 会在 Milvus 端报错
    validated_chunks = []
    for chunk in chunks:
      if chunk.get('dense_vector') and chunk.get('sparse_vector'):
        validated_chunks.append(chunk)
      else:
        self.logger.error("待入库的切块chunk的混合向量不存在")
    # 全军覆没才中断；部分有效则带着有效的继续走，被丢弃的已记日志
    if not validated_chunks:
      raise ValidationError("入库的chunk都无效", self.name)
    # 取第一条的 dense 维度：BGE-M3 固定 1024，同批一致，建集合时 FLOAT_VECTOR 字段要用
    dim = len(validated_chunks[0].get('dense_vector'))
    self.logger.info(f"导入Milvus向量数据库的有效块：{len(validated_chunks)},且chunk的向量维度{dim}")
    return validated_chunks, dim, config

  # 确保集合可用：delete_flag=True → 先删旧再建新（测试期每次全量重建）；False → 存在即复用
  def _ensure_has_collection(self, milvus_client: MilvusClient, collection_name: str, dim: int,
                             delete_flag: bool = True):
    """确保集合存在"""
    self.log_step("step2", f"准备集合 {collection_name} 创建")

    # 删除分支：开关打开且集合已存在 → 先 drop，保证每次导入从干净状态开始（测试期常用，生产环境慎开）
    if delete_flag and milvus_client.has_collection(collection_name=collection_name):
      self.logger.info(f"Milvus中的集合 {collection_name}已被删除")
      milvus_client.drop_collection(collection_name=collection_name)

    # 复用分支：集合仍存在（delete_flag=False 且未删除）→ 直接返回，不重复建
    if milvus_client.has_collection(collection_name=collection_name):
      return

    # 建集合：走到这说明集合不存在（刚被 drop 或首次运行）；dim 用于定义向量字段维度
    schema = _MilvusSchemaBuilder.build(milvus_client, dim)
    index = _MilvusIndexBuilder.build(milvus_client, collection_name)

    # schema（字段定义）+ index（向量索引）一次性提交创建
    milvus_client.create_collection(
      collection_name=collection_name,
      schema=schema,
      index_params=index
    )





if __name__ == "__main__":
  setup_logging()

  temp_dir = Path(r"C:\Users\14207\Desktop\doc\temp_dir\万用表RS-12的使用\auto")

  input_path = temp_dir / "chunks_vector.json"
  output_path = temp_dir / "chunks_vector_ids.json"

  if not input_path.exists():
    logger.error(f"找不到输入文件: {input_path}")
    exit(1)

  with open(input_path, "r", encoding="utf-8") as fh:
    content = json.load(fh)


    # 从 JSON 里取出 chunks，拼成节点需要的输入 state
    state: ImportGraphState = {
      "chunks": content.get("chunks", [])
    }

    import_milvus = ImportMilvusNode()
    result_state = import_milvus(state)

    with open(output_path, "w", encoding="utf-8") as fh:
      json.dump(result_state, fh, ensure_ascii=False, indent=4)

      logger.info(f"备份临时文件{output_path}成功")
