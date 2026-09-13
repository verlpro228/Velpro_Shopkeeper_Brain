from functools import cache, lru_cache

from knowledge.service.file_import_service import ImportFileService

# @lru_cache        #缓存满了 根据LRU（最近最少使用）策略，删除最近最少使用的缓存项
@cache              #将创建业务对象进行缓存，避免重复创建对象 缓存长期有效
def get_import_file_service():
  return ImportFileService()