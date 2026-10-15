"""查询改写领域模型：承载原始查询、改写查询和改写状态。

定义 QueryRewriteResult；该模型不保存思维链，仅允许有限长度的最终检索查询和状态。
"""

from typing import Literal

from pydantic import BaseModel, Field


RewriteStatus = Literal["REWRITTEN", "UNCHANGED", "FALLBACK", "SKIPPED"]


class QueryRewriteResult(BaseModel):
    """一次查询改写的可审计结果。"""

    original_query: str = Field(min_length=1, max_length=4000)
    rewritten_query: str = Field(min_length=1, max_length=1000)
    status: RewriteStatus
    model_name: str | None = Field(default=None, max_length=128)
