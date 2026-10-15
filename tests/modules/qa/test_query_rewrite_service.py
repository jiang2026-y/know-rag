"""查询改写服务测试：验证汽车问题改写、非汽车跳过和模型失败降级。

定义 FakeRewriteClient 与三项异步测试，确保改写服务不会因模型异常阻塞原始查询检索。
"""

import pytest

from rail_rag.modules.qa.application.query_rewrite_service import QueryRewriteService


class FakeRewriteClient:
    """返回固定结果或模拟模型失败。"""

    def __init__(self, result: str | None = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls = 0

    async def rewrite(self, question: str) -> str:
        """记录调用并返回测试结果。"""
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.result or question


@pytest.mark.asyncio
async def test_related_question_uses_rewritten_query() -> None:
    client = FakeRewriteClient("车辆刹车异响故障排查")
    result = await QueryRewriteService(client, "qwen3.5-flash").rewrite(
        "我的车刹车吱吱响怎么办", related=True
    )  # type: ignore[arg-type]

    assert result.rewritten_query == "车辆刹车异响故障排查"
    assert result.status == "REWRITTEN"
    assert client.calls == 1


@pytest.mark.asyncio
async def test_non_automotive_question_skips_model() -> None:
    client = FakeRewriteClient("不应调用")
    result = await QueryRewriteService(client, "qwen3.5-flash").rewrite(
        "今天下雨吗", related=False
    )  # type: ignore[arg-type]

    assert result.rewritten_query == "今天下雨吗"
    assert result.status == "SKIPPED"
    assert client.calls == 0


@pytest.mark.asyncio
async def test_model_failure_falls_back_to_original_query() -> None:
    client = FakeRewriteClient(error=RuntimeError("timeout"))
    result = await QueryRewriteService(client, "qwen3.5-flash").rewrite(
        "Model Y 续航多少", related=True
    )  # type: ignore[arg-type]

    assert result.rewritten_query == "Model Y 续航多少"
    assert result.status == "FALLBACK"
