"""知识检索接口：使用当前服务端 Session 执行已授权的向量检索。

定义 KnowledgeSearchRequest、KnowledgeSearchResponse、KnowledgeSearchHit 与 search_knowledge；
接口不接受任何客户端权限标签，只将可信主体传给知识检索应用服务。
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from rail_rag.bootstrap import get_knowledge_retrieval_service
from rail_rag.modules.identity.api.auth_controller import get_current_principal
from rail_rag.modules.identity.domain.principal import SessionPrincipal
from rail_rag.modules.knowledge.application.knowledge_retrieval_service import (
    KnowledgeRetrievalService,
)


router = APIRouter(prefix="/knowledge", tags=["knowledge"])


class KnowledgeSearchRequest(BaseModel):
    """知识检索请求，只允许提交问题和受限返回数量。"""

    question: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=5, ge=1, le=20)


class KnowledgeSearchHit(BaseModel):
    """一个已通过权限过滤的知识分片。"""

    chunk_id: str
    text: str
    score: float
    document_id: int | None


class KnowledgeSearchResponse(BaseModel):
    """当前身份可见的知识分片检索结果。"""

    hits: list[KnowledgeSearchHit]


@router.post("/search", response_model=KnowledgeSearchResponse)
async def search_knowledge(
    payload: KnowledgeSearchRequest,
    principal: Annotated[SessionPrincipal, Depends(get_current_principal)],
    service: Annotated[
        KnowledgeRetrievalService, Depends(get_knowledge_retrieval_service)
    ],
) -> KnowledgeSearchResponse:
    """执行服务端权限过滤后的 Elasticsearch 向量检索。"""
    hits = await service.search(principal, payload.question, limit=payload.limit)
    return KnowledgeSearchResponse(
        hits=[KnowledgeSearchHit(**hit.__dict__) for hit in hits]
    )
