from functools import lru_cache

from typing import Literal

from pydantic import AnyHttpUrl, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """由环境变量提供的运行时配置。"""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "know-engine"
    minio_secure: bool = False
    minio_public_base_url: AnyHttpUrl | None = None
    minio_presigned_url_expiry_seconds: int = Field(default=900, ge=1, le=604800)

    mysql_host: str = "127.0.0.1"
    mysql_port: int = Field(default=3306, ge=1, le=65535)
    mysql_database: str = "know_engine"
    mysql_user: str = "know_engine"
    mysql_password: SecretStr = SecretStr("know_engine")
    mysql_connect_timeout_seconds: int = Field(default=5, gt=0)

    neo4j_enabled: bool = False
    neo4j_uri: str = "bolt://127.0.0.1:7687"
    neo4j_database: str = "neo4j"
    neo4j_user: str = "llmcopy_reader"
    neo4j_password: SecretStr | None = None
    neo4j_connect_timeout_seconds: float = Field(default=5, gt=0)

    snowflake_worker_id: int = Field(default=0, ge=0, le=1023)

    redis_host: str = "127.0.0.1"
    redis_port: int = Field(default=6379, ge=1, le=65535)
    redis_password: SecretStr | None = None
    redis_database: int = Field(default=0, ge=0)
    redis_connect_timeout_seconds: float = Field(default=5, gt=0)
    redis_lock_lease_seconds: int = Field(default=30, ge=3)

    # Celery/Relier 使用独立任务 Redis，不能复用认证 Session Redis。
    task_redis_url: str = "redis://:123456@127.0.0.1:6380/0"
    task_redis_password: SecretStr | None = None
    task_max_auto_retries: int = Field(default=3, ge=0, le=20)
    task_dlq_retention_days: int = Field(default=7, ge=1, le=365)

    auth_session_cookie_name: str = "llmcopy_session"
    auth_session_ttl_seconds: int = Field(default=28800, ge=300, le=604800)
    auth_session_cookie_secure: bool = False
    auth_password_hash_iterations: int = Field(default=600000, ge=100000)
    auth_argon2_memory_cost_kib: int = Field(default=19456, ge=8192)
    auth_argon2_time_cost: int = Field(default=2, ge=1)
    auth_argon2_parallelism: int = Field(default=1, ge=1)
    internal_hr_service_token: SecretStr | None = None
    internal_hr_service_name: str = Field(default="hr-system", min_length=1, max_length=64)

    elasticsearch_host: AnyHttpUrl = "http://127.0.0.1:9200"
    elasticsearch_index: str = "know-engine"
    elasticsearch_connect_timeout_seconds: float = Field(default=5, gt=0)
    elasticsearch_response_timeout_seconds: float = Field(default=30, gt=0)
    # 全文检索分析器：生产建议 analysis-ik 的 ik_smart；
    # 未安装 IK 的隔离评测环境可显式切到 standard。
    elasticsearch_text_analyzer: str = "ik_smart"

    # 父块三级缓存的 Redis 层：复用认证 Session 的 Redis 实例，只缓存分片正文，
    # 不缓存任何权限结论；未命中时仍然回源 MySQL 并重新执行二次授权。
    segment_cache_enabled: bool = True
    segment_cache_ttl_seconds: int = Field(default=30, ge=1, le=3600)

    embedding_base_url: AnyHttpUrl = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    embedding_model_name: str = "text-embedding-v4"
    embedding_api_key: SecretStr | None = None
    embedding_dimensions: int = Field(default=1536, gt=0)
    embedding_max_segments_per_batch: int = Field(default=9, ge=1, le=10)
    embedding_connect_timeout_seconds: float = Field(default=15, gt=0)
    embedding_response_timeout_seconds: float = Field(default=60, gt=0)

    chat_title_model_name: str = "qwen3.5-flash"
    chat_title_max_length: int = Field(default=20, ge=1, le=100)
    chat_title_connect_timeout_seconds: float = Field(default=5, gt=0)
    chat_title_response_timeout_seconds: float = Field(default=20, gt=0)

    chat_intent_model_name: str = "qwen3.5-flash"
    chat_intent_connect_timeout_seconds: float = Field(default=5, gt=0)
    chat_intent_response_timeout_seconds: float = Field(default=20, gt=0)
    chat_intent_max_completion_tokens: int = Field(default=256, ge=64, le=1024)

    chat_query_rewrite_model_name: str = "qwen3.5-flash"
    chat_query_rewrite_connect_timeout_seconds: float = Field(default=5, gt=0)
    chat_query_rewrite_response_timeout_seconds: float = Field(default=20, gt=0)
    chat_query_rewrite_max_completion_tokens: int = Field(default=256, ge=64, le=1024)

    chat_query_router_model_name: str = "qwen3.5-flash"
    chat_query_router_connect_timeout_seconds: float = Field(default=5, gt=0)
    chat_query_router_response_timeout_seconds: float = Field(default=20, gt=0)
    chat_query_router_max_completion_tokens: int = Field(default=256, ge=64, le=1024)

    chat_answer_model_name: str = "qwen3.7-plus"
    chat_answer_connect_timeout_seconds: float = Field(default=10, gt=0)
    chat_answer_response_timeout_seconds: float = Field(default=120, gt=0)
    chat_answer_max_completion_tokens: int = Field(default=1024, ge=1, le=4096)
    chat_sse_heartbeat_seconds: int = Field(default=15, ge=1, le=60)
    chat_rag_top_k: int = Field(default=5, ge=1, le=20)
    chat_rag_vector_top_k: int = Field(default=20, ge=1, le=100)
    chat_rag_full_text_top_k: int = Field(default=20, ge=1, le=100)
    chat_rag_rrf_k: int = Field(default=60, ge=1, le=1000)
    chat_rag_context_max_characters: int = Field(default=12000, ge=1000, le=100000)

    # 多轮记忆：只读取已成功持久化的最近若干条消息，并按字符预算从最近往前裁剪；
    # 历史读取失败只降级为无历史，不阻断回答主链路。
    chat_history_max_messages: int = Field(default=10, ge=0, le=50)
    chat_history_max_characters: int = Field(default=4000, ge=0, le=20000)

    chat_rerank_base_url: AnyHttpUrl | None = None
    chat_rerank_api_key: SecretStr | None = None
    chat_rerank_model_name: str = "qwen3-rerank"
    chat_rerank_connect_timeout_seconds: float = Field(default=5, gt=0)
    chat_rerank_response_timeout_seconds: float = Field(default=30, gt=0)
    chat_rerank_top_k: int = Field(default=5, ge=1, le=20)
    chat_rerank_document_max_characters: int = Field(default=4000, ge=100, le=16000)

    # RAGAS 复用百炼的现有 OpenAI 兼容连接（embedding_base_url 与 embedding_api_key）。
    ragas_evaluator_model_name: str = "deepseek-v4.1-flash"
    ragas_evaluator_max_tokens: int = Field(default=16384, ge=256, le=16384)
    ragas_evaluator_connect_timeout_seconds: float = Field(default=10, gt=0)
    ragas_evaluator_response_timeout_seconds: float = Field(default=120, gt=0)
    ragas_evaluator_max_concurrency: int = Field(default=2, ge=1, le=20)

    mineru_api_base_url: AnyHttpUrl = "https://mineru.net"
    mineru_api_token: SecretStr | None = None
    mineru_model_version: Literal["pipeline", "vlm"] = "pipeline"
    mineru_connect_timeout_seconds: float = Field(default=30, gt=0)
    mineru_response_timeout_seconds: float = Field(default=300, gt=0)
    mineru_task_poll_interval_seconds: float = Field(default=2, gt=0)
    mineru_task_timeout_seconds: float = Field(default=900, gt=0)
    mineru_result_download_retry_attempts: int = Field(default=7, ge=1)
    mineru_result_download_retry_delay_seconds: float = Field(default=5, gt=0)
    max_upload_size_bytes: int = Field(default=100 * 1024 * 1024, gt=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
