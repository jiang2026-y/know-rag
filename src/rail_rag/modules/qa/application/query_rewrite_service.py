"""查询改写应用服务：执行改写、失败降级并构造可持久化结果。

定义 QueryRewriteService；汽车相关问题调用查询改写客户端，模型失败时使用原查询，
非汽车问题不调用模型，避免向企业检索链路传递无关输入。
"""

from rail_rag.modules.qa.domain.query_rewrite import QueryRewriteResult
from rail_rag.modules.qa.infrastructure.dashscope_query_rewrite_client import QueryRewriteClient


class QueryRewriteService:
    """将模型改写结果转换为安全、有限长度的检索查询。"""

    def __init__(self, client: QueryRewriteClient, model_name: str) -> None:
        self._client = client
        self._model_name = model_name

    async def rewrite(self, question: str, *, related: bool) -> QueryRewriteResult:
        """相关问题调用模型，异常时安全使用原查询。"""
        original = question.strip()
        if not original:
            raise ValueError("Question must not be empty")
        if not related:
            return QueryRewriteResult(original_query=original, rewritten_query=original, status="SKIPPED")
        try:
            rewritten = (await self._client.rewrite(original)).strip()
            if not rewritten:
                raise ValueError("Empty rewritten query")
            return QueryRewriteResult(
                original_query=original,
                rewritten_query=rewritten,
                status="UNCHANGED" if rewritten == original else "REWRITTEN",
                model_name=self._model_name,
            )
        except (RuntimeError, ValueError):
            return QueryRewriteResult(original_query=original, rewritten_query=original, status="FALLBACK", model_name=self._model_name)
