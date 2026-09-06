import logging
import threading

from langchain_openai import ChatOpenAI
from openai import OpenAI
from pymilvus.model.hybrid import BGEM3EmbeddingFunction
from FlagEmbedding import FlagReranker
from knowledge.utils.client.base import BaseClientManager, logger
import logging
from typing import Optional


class AIClients(BaseClientManager):
    """
    AI 模型类客户端： OpenAI(VLM)

    全项目统一的模型客户端发放入口，每类客户端 = 缓存槽 + 锁 + get_xxx() + _create_xxx()。
    get_xxx() 内部走 _get_or_create 双重检查锁：首次调用才真正创建，之后全进程复用同一实例。
    """
    _openai_client: Optional[OpenAI] = None   # 缓存槽：None 表示尚未创建
    _openai_lock = threading.Lock()           # 锁：防止两个线程同时发现"没缓存"而各建一个

    @classmethod
    def get_openai(cls) -> OpenAI:
        # 对外入口：拿 VLM 用的原生 OpenAI SDK 客户端（md_img 节点调 Qwen-VL 用这个）
        return cls._get_or_create("_openai_client", cls._openai_lock, cls._create_openai)

    @classmethod
    def _create_openai(cls) -> OpenAI:
        # 真正的创建逻辑，只在第一次 get 时执行一次
        try:
            api_key = cls._require_env("DASHSCOPE_API_KEY")   # 从 .env 读密钥，缺失直接抛 EnvironmentError
            base_url = cls._require_env("OPENAI_API_BASE")    # DashScope 的 OpenAI 兼容接口地址
            client = OpenAI(
                api_key=api_key,
                base_url=base_url
            )
            logger.info(f"OpenAI API 创建成功:{base_url}")
            return client
        except EnvironmentError:
            raise                        # 配置问题：原样上抛，提示去改 .env
        except Exception as e:
            logger.error(f"OpenAI API 创建失败:{e}")
            raise ConnectionError(f"OpenAI连接失败:{e}") from e   # 其他失败：包装成统一异常，from e 保留原始堆栈

    """
    LLM客户端：文本生成（LangChain 的 ChatOpenAI 封装，配合 prompt 模板链式调用）
    """
    # 按输出格式分成两个独立缓存槽：普通文本一个、强制JSON输出一个，互不干扰
    _openai_llm_text_client: Optional[ChatOpenAI] = None
    _openai_llm_text_lock = threading.Lock()

    _openai_llm_json_client: Optional[ChatOpenAI] = None
    _openai_llm_json_lock = threading.Lock()

    @classmethod
    def get_llm_openai(cls, response_format: bool=True) -> ChatOpenAI:
        if response_format:
            #_create_llm_openai(response_format) 加了 () 立即执行，应改为 lambda: 延迟执行
            # lambda 包一层：把"怎么创建"推迟到 _get_or_create 确认无缓存时才执行
            return cls._get_or_create("_openai_llm_json_client", cls._openai_llm_json_lock, lambda: cls._create_llm_openai(response_format))
        else:
            return cls._get_or_create("_openai_llm_text_client", cls._openai_llm_text_lock, lambda: cls._create_llm_openai(response_format))
    @classmethod
    def _create_llm_openai(cls, response_format) -> ChatOpenAI:
        try:
            api_key = cls._require_env("DASHSCOPE_API_KEY")
            base_url = cls._require_env("OPENAI_API_BASE")
            model_name = cls._require_env("LLM_DEFAULT_MODEL")   # 默认对话模型名（LLM_DEFAULT_MODEL）

            model_kwargs = {}
            if response_format:
                # JSON模式：让模型必定返回可 json.loads 的文本，商品名识别等结构化场景用
                model_kwargs['response_format'] = {"type": "json_object"}

            client = ChatOpenAI(
                model_name=model_name,
                openai_api_key=api_key,
                openai_api_base=base_url,
                temperature=0,             # 温度0：输出稳定可复现，适合抽取/判断类任务
                model_kwargs=model_kwargs
            )
            logger.info(f"ChatOpenAI LLM 客户端初始化成功")
            return client
        except EnvironmentError:
            raise
        except Exception as e:
            logger.error(f"ChatOpenAI LLM 客户端初始化失败:{e}")
            raise ConnectionError(f"ChatOpenAI LLM 连接失败:{e}") from e

    """
    BGE-M3客户端：本地向量模型（导入时切片向量化、查询时问题向量化）
    """
    # 加载一次要把几百MB权重读进显存、耗时几十秒，必须全进程单例，绝不能每次请求重建
    _bge_m3_client: Optional[BGEM3EmbeddingFunction] = None
    _bge_m3_lock = threading.Lock()

    @classmethod
    def get_bge_m3_client(cls) -> BGEM3EmbeddingFunction:
        return cls._get_or_create("_bge_m3_client", cls._bge_m3_lock, cls._create_bge_m3_client)

    @classmethod
    def _create_bge_m3_client(cls) -> BGEM3EmbeddingFunction:
        try:
            model_name = cls._require_env("BGE_M3_PATH")     # 本地模型权重目录（不走在线下载）
            device = cls._require_env("BGE_DEVICE")          # "cuda" / "cpu"
            fp16 = cls._require_env("BGE_FP16")              # 是否半精度推理

            bge_m3_ef = BGEM3EmbeddingFunction(
                model_name=model_name,
                device=device,
                use_fp16=fp16,
            )
            logger.info(f"bge_m3客户端初始化成功")
            return bge_m3_ef
        except EnvironmentError:
            raise
        except Exception as e:
            logger.error(f"bge_m3客户端初始化失败:{e}")
            raise ConnectionError(f"bge_m3客户端创建失败:{e}") from e


    """
    BGE-M3重排序模型客户端：检索后对候选切片精排（query-文档逐对打分，取top-k给LLM）
    """
    _bge_m3_rerank_client: Optional[FlagReranker] = None
    _bge_m3_rerank_lock = threading.Lock()

    @classmethod
    def get_bge_m3_rerank_client(cls) -> FlagReranker:
        return cls._get_or_create("_bge_m3_rerank_client", cls._bge_m3_rerank_lock, cls._create_bge_m3_rerank_client)

    @classmethod
    def _create_bge_m3_rerank_client(cls) -> FlagReranker:
        try:
            model_name_or_path = cls._require_env("BGE_RERANKER_LARGE")
            device = cls._require_env("BGE_DEVICE")
            fp16_str = cls._require_env("BGE_FP16")
            # 环境变量读出来永远是字符串，必须手动转bool——"false"是真值，直接用会把开关判反
            fp16 = fp16_str.lower() in ("true","1")
            

            reranker = FlagReranker(
                model_name_or_path=model_name_or_path,
                #model_name_or_path="D:\\ai_models\\modelscope_cache\\models\\BAAI\\BAAI\\bge-reranker-large",
                device=device,  # GPU 加速
                use_fp16=fp16  # 半精度推理
            )
            logger.info(f"bge_m3_rerank客户端初始化成功")
            return reranker
        except EnvironmentError:
            raise
        except Exception as e:
            logger.error(f"bge_m3_rerank客户端初始化失败:{e}")
            raise ConnectionError(f"bge_m3_rerank客户端创建失败:{e}") from e


if __name__ == "__main__":
    print(AIClients.get_bge_m3_rerank_client())