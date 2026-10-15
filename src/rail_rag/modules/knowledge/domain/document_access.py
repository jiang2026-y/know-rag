"""知识文档访问领域规则：定义 P0 文档标签及从可信主体推导检索范围的函数。

定义 DocumentAccessLabel、document_access_labels、can_review_documents 和
can_view_pending_document，分别用于校验文档标签、计算 ES 过滤标签、判断领导审核权限
以及判断待审核文档的查看权限。
"""

from typing import Literal

from rail_rag.modules.identity.domain.principal import SessionPrincipal


DocumentAccessLabel = Literal["VISITOR", "STAFF", "LEADER"]
DOCUMENT_ACCESS_LABELS: frozenset[str] = frozenset({"VISITOR", "STAFF", "LEADER"})


def document_access_labels(principal: SessionPrincipal) -> tuple[DocumentAccessLabel, ...]:
    """根据已实时校验的 Session 主体返回其 P0 文档检索范围。"""
    if principal.principal_type != "STAFF":
        return ("VISITOR",)
    if principal.job == "staff":
        return ("VISITOR", "STAFF")
    if principal.job == "leader":
        return ("VISITOR", "STAFF", "LEADER")
    raise ValueError("Unsupported staff job for document access")


def can_review_documents(principal: SessionPrincipal) -> bool:
    """仅在职 leader 的可信主体可以审核知识文档。"""
    return principal.principal_type == "STAFF" and principal.job == "leader"


def can_view_pending_document(principal: SessionPrincipal, upload_user: str | None) -> bool:
    """待审核或已驳回文档仅允许其上传员工及领导查看。"""
    return can_review_documents(principal) or (
        principal.principal_type == "STAFF" and principal.principal_id == upload_user
    )
