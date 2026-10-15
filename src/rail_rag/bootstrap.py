"""应用依赖组装：集中创建身份、知识和会话模块所需的应用服务与基础设施适配器。

定义各模块的缓存工厂函数；API 层通过这些函数获取服务，不直接负责构造 MySQL、Redis、
MinIO、Elasticsearch、MinerU 或模型客户端。
"""

from functools import lru_cache

from rail_rag.config.settings import get_settings
from rail_rag.modules.conversation.application.conversation_service import (
    ChatConversationService,
)
from rail_rag.modules.conversation.infrastructure.conversation_repository import (
    ChatConversationRepository,
)
from rail_rag.modules.conversation.infrastructure.feedback_repository import FeedbackRepository
from rail_rag.modules.conversation.application.feedback_service import FeedbackService
from rail_rag.modules.conversation.infrastructure.dashscope_title_client import (
    DashScopeTitleClient,
)
from rail_rag.modules.conversation.infrastructure.dashscope_answer_client import (
    DashScopeAnswerClient,
)
from rail_rag.modules.qa.application.intent_recognition_service import (
    IntentRecognitionService,
)
from rail_rag.modules.qa.application.query_rewrite_service import QueryRewriteService
from rail_rag.modules.qa.application.query_router_service import QueryRouterService
from rail_rag.modules.qa.application.query_data_source_service import QueryDataSourceService
from rail_rag.modules.qa.application.rerank_service import RerankService
from rail_rag.modules.qa.infrastructure.dashscope_intent_client import (
    DashScopeIntentClient,
)
from rail_rag.modules.qa.infrastructure.dashscope_query_rewrite_client import (
    DashScopeQueryRewriteClient,
)
from rail_rag.modules.qa.infrastructure.dashscope_query_router_client import (
    DashScopeQueryRouterClient,
)
from rail_rag.modules.qa.infrastructure.dashscope_rerank_client import DashScopeRerankClient
from rail_rag.modules.qa.infrastructure.neo4j_graph_retriever import Neo4jGraphRetriever
from rail_rag.modules.vehicle.infrastructure.my_car_record_retriever import MyCarRecordRetriever
from rail_rag.modules.conversation.application.chat_answer_service import (
    ChatAnswerService,
)
from rail_rag.modules.identity.infrastructure.auth_repository import (
    AuthenticationRepository,
)
from rail_rag.modules.identity.infrastructure.password_hasher import PasswordHasher
from rail_rag.modules.identity.infrastructure.session_service import RedisSessionService
from rail_rag.modules.identity.infrastructure.staff_access_repository import (
    StaffAccessRepository,
)
from rail_rag.modules.identity.application.staff_access_service import StaffAccessService
from rail_rag.modules.identity.application.staff_password_provision_service import (
    StaffPasswordProvisionService,
)
from rail_rag.modules.identity.infrastructure.staff_password_provision_repository import (
    StaffPasswordProvisionRepository,
)
from rail_rag.modules.vehicle.application.vehicle_scope_service import VehicleScopeService
from rail_rag.modules.vehicle.infrastructure.vehicle_scope_repository import (
    VehicleScopeRepository,
)
from rail_rag.modules.knowledge.application.document_task_service import (
    DocumentTaskService,
)
from rail_rag.modules.knowledge.application.task_dead_letter_service import (
    TaskDeadLetterService,
)
from rail_rag.modules.knowledge.application.document_upload_service import (
    DocumentUploadService,
)
from rail_rag.modules.knowledge.application.document_vectorization_service import (
    DocumentVectorizationService,
)
from rail_rag.modules.knowledge.application.markdown_access_service import (
    MarkdownAccessService,
)
from rail_rag.modules.knowledge.application.document_read_access_service import (
    DocumentReadAccessService,
)
from rail_rag.modules.knowledge.application.document_review_queue_service import (
    DocumentReviewQueueService,
)
from rail_rag.modules.knowledge.application.document_review_service import (
    DocumentReviewService,
)
from rail_rag.modules.knowledge.application.knowledge_retrieval_service import (
    KnowledgeRetrievalService,
)
from rail_rag.modules.knowledge.application.context_expansion_service import (
    ContextExpansionService,
)
from rail_rag.modules.knowledge.domain.markdown_parent_child_splitter import (
    MarkdownParentChildSplitter,
)
from rail_rag.modules.knowledge.domain.document_split_param import DocumentSplitParam
from rail_rag.modules.knowledge.domain.document_splitter_factory import (
    build_document_splitter,
)
from rail_rag.modules.knowledge.domain.snowflake_id_generator import (
    get_snowflake_id_generator,
)
from rail_rag.modules.knowledge.infrastructure.dashscope_embedding_client import (
    DashScopeEmbeddingClient,
)
from rail_rag.modules.knowledge.infrastructure.document_task_repository import (
    DocumentTaskRepository,
)
from rail_rag.modules.knowledge.infrastructure.relier_document_task_dispatcher import (
    RelierDocumentTaskDispatcher,
)
from rail_rag.modules.knowledge.infrastructure.elasticsearch_embedding_store import (
    ElasticsearchEmbeddingStore,
)
from rail_rag.modules.knowledge.infrastructure.knowledge_document_repository import (
    KnowledgeDocumentRepository,
)
from rail_rag.modules.knowledge.infrastructure.knowledge_segment_repository import (
    KnowledgeSegmentRepository,
)
from rail_rag.modules.knowledge.infrastructure.mineru_client import MinerUClient
from rail_rag.modules.knowledge.infrastructure.minio_storage_service import (
    MinioStorageService,
)
from rail_rag.modules.knowledge.infrastructure.redis_distributed_lock import (
    RedisDistributedLock,
)
from rail_rag.modules.knowledge.infrastructure.redis_segment_text_cache import (
    RedisSegmentTextCache,
)


@lru_cache
def get_password_hasher() -> PasswordHasher:
    """创建身份认证所需的密码哈希器。"""
    settings = get_settings()
    return PasswordHasher(
        legacy_pbkdf2_iterations=settings.auth_password_hash_iterations,
        argon2_memory_cost_kib=settings.auth_argon2_memory_cost_kib,
        argon2_time_cost=settings.auth_argon2_time_cost,
        argon2_parallelism=settings.auth_argon2_parallelism,
    )


@lru_cache
def get_authentication_repository() -> AuthenticationRepository:
    """创建顾客与员工账号认证仓储。"""
    return AuthenticationRepository(get_settings(), get_password_hasher())


@lru_cache
def get_session_service() -> RedisSessionService:
    """创建 Redis 服务端会话服务。"""
    return RedisSessionService(get_settings())


@lru_cache
def get_staff_access_repository() -> StaffAccessRepository:
    """创建员工访问更新、幂等和审计仓储。"""
    return StaffAccessRepository(get_settings())


@lru_cache
def get_staff_access_service() -> StaffAccessService:
    """创建管理员员工访问更新用例。"""
    return StaffAccessService(get_staff_access_repository(), get_session_service())


@lru_cache
def get_staff_password_provision_repository() -> StaffPasswordProvisionRepository:
    """创建 HR 初始密码、幂等和审计仓储。"""
    return StaffPasswordProvisionRepository(get_settings())


@lru_cache
def get_staff_password_provision_service() -> StaffPasswordProvisionService:
    """创建 HR 内部员工初始密码用例。"""
    return StaffPasswordProvisionService(
        get_settings(),
        get_staff_password_provision_repository(),
        get_session_service(),
        get_password_hasher(),
    )


@lru_cache
def get_vehicle_scope_repository() -> VehicleScopeRepository:
    """创建只读车辆范围仓储。"""
    return VehicleScopeRepository(get_settings())


@lru_cache
def get_vehicle_scope_service() -> VehicleScopeService:
    """创建当前顾客车辆范围用例。"""
    return VehicleScopeService(get_vehicle_scope_repository())


@lru_cache
def get_document_upload_service() -> DocumentUploadService:
    """创建文档上传与 MinerU 转换服务。"""
    settings = get_settings()
    return DocumentUploadService(
        settings=settings,
        storage=get_minio_storage_service(),
        mineru=get_mineru_client(),
    )


@lru_cache
def get_markdown_access_service() -> MarkdownAccessService:
    """创建 Markdown 预览与受控下载服务。"""
    return MarkdownAccessService(get_settings(), get_minio_storage_service())


@lru_cache
def get_document_read_access_service() -> DocumentReadAccessService:
    """创建知识文档 Markdown 的状态和权限校验服务。"""
    return DocumentReadAccessService(
        get_settings(), get_knowledge_document_repository(), get_document_task_repository()
    )


@lru_cache
def get_document_review_queue_service() -> DocumentReviewQueueService:
    """创建按可信员工身份过滤的文档审核队列用例。"""
    return DocumentReviewQueueService(get_knowledge_document_repository())


@lru_cache
def get_knowledge_document_repository() -> KnowledgeDocumentRepository:
    """创建知识文档仓储。"""
    return KnowledgeDocumentRepository(get_settings())


@lru_cache
def get_document_task_repository() -> DocumentTaskRepository:
    """创建文档任务仓储。"""
    return DocumentTaskRepository(get_settings())


@lru_cache
def get_knowledge_segment_repository() -> KnowledgeSegmentRepository:
    """创建知识分段仓储。"""
    return KnowledgeSegmentRepository(get_settings())


@lru_cache
def get_elasticsearch_embedding_store() -> ElasticsearchEmbeddingStore:
    """创建 Elasticsearch 向量存储适配器。"""
    return ElasticsearchEmbeddingStore(
        get_settings(), embedder=get_dashscope_embedding_client()
    )


@lru_cache
def get_redis_distributed_lock() -> RedisDistributedLock:
    """创建 Redis 分布式锁适配器。"""
    return RedisDistributedLock(get_settings())


@lru_cache
def get_redis_segment_text_cache() -> RedisSegmentTextCache:
    """创建父块正文二级缓存；未命中或 Redis 不可用时回源 MySQL。"""
    return RedisSegmentTextCache(get_settings())


@lru_cache
def get_minio_storage_service() -> MinioStorageService:
    """创建 MinIO 私有对象存储适配器。"""
    return MinioStorageService(get_settings())


@lru_cache
def get_mineru_client() -> MinerUClient:
    """创建 MinerU 文档解析客户端。"""
    return MinerUClient(get_settings())


@lru_cache
def get_dashscope_embedding_client() -> DashScopeEmbeddingClient:
    """创建 DashScope 向量化客户端。"""
    return DashScopeEmbeddingClient(get_settings())


@lru_cache
def get_document_vectorization_service() -> DocumentVectorizationService:
    """创建文档向量化应用服务。"""
    return DocumentVectorizationService(
        get_knowledge_document_repository(),
        get_knowledge_segment_repository(),
        get_elasticsearch_embedding_store(),
        get_redis_distributed_lock(),
    )


@lru_cache
def get_document_review_service() -> DocumentReviewService:
    """创建领导审核知识文档并触发向量化的应用服务。"""
    return DocumentReviewService(
        get_knowledge_document_repository(), get_document_vectorization_service()
    )


@lru_cache
def get_knowledge_retrieval_service() -> KnowledgeRetrievalService:
    """创建仅按可信主体范围检索知识分片的应用服务。"""
    return KnowledgeRetrievalService(
        get_elasticsearch_embedding_store(),
        ContextExpansionService(
            get_knowledge_segment_repository(), get_redis_segment_text_cache()
        ),
        get_settings(),
    )


@lru_cache
def get_document_task_service() -> DocumentTaskService:
    """创建文档处理任务应用服务。"""
    settings = get_settings()

    def splitter_factory(param: DocumentSplitParam | None):
        """按版本级切片参数构造切片器，保持雪花 ID 生成器单例。"""
        return build_document_splitter(
            param, id_generator=get_snowflake_id_generator(settings.snowflake_worker_id)
        )

    return DocumentTaskService(
        settings,
        get_document_upload_service(),
        get_knowledge_document_repository(),
        get_document_task_repository(),
        get_knowledge_segment_repository(),
        get_document_vectorization_service(),
        get_redis_distributed_lock(),
        RelierDocumentTaskDispatcher(),
        MarkdownParentChildSplitter(
            id_generator=get_snowflake_id_generator(settings.snowflake_worker_id)
        ),
        splitter_factory=splitter_factory,
        segment_cache=get_redis_segment_text_cache(),
    )


@lru_cache
def get_task_dead_letter_service() -> TaskDeadLetterService:
    """创建 Relier 死信任务的七天保留与 leader 重放应用服务。"""
    return TaskDeadLetterService(get_settings(), get_document_task_service())


@lru_cache
def get_chat_conversation_repository() -> ChatConversationRepository:
    """创建会话持久化仓储。"""
    return ChatConversationRepository(get_settings())


@lru_cache
def get_feedback_repository() -> FeedbackRepository:
    """创建回答赞踩反馈与幂等持久化仓储。"""
    return FeedbackRepository(get_settings())


@lru_cache
def get_feedback_service() -> FeedbackService:
    """创建按可信会话归属保存回答反馈的应用服务。"""
    return FeedbackService(get_feedback_repository())


@lru_cache
def get_dashscope_title_client() -> DashScopeTitleClient:
    """创建会话标题模型客户端。"""
    return DashScopeTitleClient(get_settings())


@lru_cache
def get_dashscope_answer_client() -> DashScopeAnswerClient:
    """创建 qwen3.7-plus 流式主回答客户端。"""
    return DashScopeAnswerClient(get_settings())


@lru_cache
def get_dashscope_intent_client() -> DashScopeIntentClient:
    """创建 qwen3.5-flash 非流式结构化意图识别客户端。"""
    return DashScopeIntentClient(get_settings())


@lru_cache
def get_intent_recognition_service() -> IntentRecognitionService:
    """创建带 Schema 校验、退避重试和状态审计的意图识别用例。"""
    return IntentRecognitionService(get_dashscope_intent_client())


@lru_cache
def get_dashscope_query_rewrite_client() -> DashScopeQueryRewriteClient:
    """创建 qwen3.5-flash 查询改写客户端。"""
    return DashScopeQueryRewriteClient(get_settings())


@lru_cache
def get_query_rewrite_service() -> QueryRewriteService:
    """创建带失败降级的查询改写应用服务。"""
    settings = get_settings()
    return QueryRewriteService(
        get_dashscope_query_rewrite_client(), settings.chat_query_rewrite_model_name
    )


@lru_cache
def get_dashscope_query_router_client() -> DashScopeQueryRouterClient:
    """创建 qwen3.5-flash 查询路由客户端。"""
    return DashScopeQueryRouterClient(get_settings())


@lru_cache
def get_query_router_service() -> QueryRouterService:
    """创建带多路默认组合和安全空路由降级的查询路由服务。"""
    return QueryRouterService(get_dashscope_query_router_client())


@lru_cache
def get_neo4j_graph_retriever() -> Neo4jGraphRetriever:
    """创建仅可读取公开车型图谱的 Neo4j 适配器。"""
    return Neo4jGraphRetriever(get_settings())


@lru_cache
def get_my_car_record_retriever() -> MyCarRecordRetriever:
    """创建仅按当前车主身份查询 my_car 的关系库适配器。"""
    return MyCarRecordRetriever(get_settings())


@lru_cache
def get_query_data_source_service() -> QueryDataSourceService:
    """创建并行召回、统一 RRF 的知识库、图数据库和当前车主关系库编排服务。"""
    return QueryDataSourceService(
        get_knowledge_retrieval_service(),
        get_neo4j_graph_retriever(),
        get_my_car_record_retriever(),
        get_settings(),
    )


@lru_cache
def get_dashscope_rerank_client() -> DashScopeRerankClient:
    """创建仅接收已授权证据的 qwen3-rerank 客户端。"""
    return DashScopeRerankClient(get_settings())


@lru_cache
def get_rerank_service() -> RerankService:
    """创建带未配置/故障降级策略的统一证据重排服务。"""
    settings = get_settings()
    return RerankService(
        get_dashscope_rerank_client(),
        enabled=get_dashscope_rerank_client().configured,
        top_k=settings.chat_rerank_top_k,
        document_max_characters=settings.chat_rerank_document_max_characters,
    )


@lru_cache
def get_chat_conversation_service() -> ChatConversationService:
    """创建会话与异步标题生成应用服务。"""
    settings = get_settings()
    return ChatConversationService(
        get_chat_conversation_repository(),
        get_dashscope_title_client(),
        title_max_length=settings.chat_title_max_length,
    )


@lru_cache
def get_chat_answer_service() -> ChatAnswerService:
    """创建受控检索、主回答和成功持久化编排服务。"""
    return ChatAnswerService(
        get_settings(),
        get_chat_conversation_repository(),
        get_knowledge_retrieval_service(),
        get_dashscope_answer_client(),
        get_intent_recognition_service(),
        get_query_rewrite_service(),
        get_query_router_service(),
        get_query_data_source_service(),
        get_rerank_service(),
    )
