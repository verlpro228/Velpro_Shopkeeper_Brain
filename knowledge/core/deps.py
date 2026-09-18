from functools import lru_cache, cache
from knowledge.service.file_import_service import ImportFileService
from knowledge.service.query_service import QueryService



@cache
def get_import_file_service() -> ImportFileService:
  return ImportFileService()


@lru_cache
def get_query_service() -> QueryService:  
  return QueryService()
