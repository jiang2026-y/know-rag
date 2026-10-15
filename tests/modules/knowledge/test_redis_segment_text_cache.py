"""父块正文 Redis 缓存测试：验证命中、空值缓存、按文档失效与故障降级。

定义 FakeRedis 替身与五项测试，覆盖 TTL 写入与文档索引、空正文防击穿、
按文档批量失效、Redis 故障静默退化以及关闭缓存时不触碰 Redis。
"""

from __future__ import annotations

import pytest
from redis.exceptions import RedisError

from rail_rag.config.settings import Settings
from rail_rag.modules.knowledge.infrastructure.redis_segment_text_cache import (
    RedisSegmentTextCache,
)


class FakeRedis:
    """最小可用的异步 Redis 替身，支持字符串、集合与过期时间。"""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.values: dict[str, str] = {}
        self.sets: dict[str, set[str]] = {}
        self.expirations: dict[str, int] = {}

    def _check(self) -> None:
        if self.fail:
            raise RedisError("redis unavailable")

    async def get(self, key: str) -> str | None:
        self._check()
        return self.values.get(key)

    async def set(self, key: str, value: str, *, ex: int | None = None) -> None:
        self._check()
        self.values[key] = value
        if ex is not None:
            self.expirations[key] = ex

    async def sadd(self, key: str, *values: str) -> None:
        self._check()
        self.sets.setdefault(key, set()).update(values)

    async def expire(self, key: str, seconds: int) -> None:
        self._check()
        self.expirations[key] = seconds

    async def smembers(self, key: str) -> set[str]:
        self._check()
        return set(self.sets.get(key, set()))

    async def delete(self, *keys: str) -> None:
        self._check()
        for key in keys:
            self.values.pop(key, None)
            self.sets.pop(key, None)


def build_cache(
    client: FakeRedis, *, enabled: bool = True, ttl: int = 30
) -> RedisSegmentTextCache:
    """用替身客户端构造缓存适配器。"""
    settings = Settings(
        segment_cache_enabled=enabled, segment_cache_ttl_seconds=ttl
    )
    return RedisSegmentTextCache(settings, client=client)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_writes_and_reads_cached_text_with_ttl_and_document_index() -> None:
    client = FakeRedis()
    cache = build_cache(client)

    await cache.set_text("chunk-1", "正文", document_id=7)

    assert await cache.get_text("chunk-1") == "正文"
    assert client.expirations["rail_rag:cache:segment:chunk-1"] == 30
    assert client.sets["rail_rag:cache:segment:index:7"] == {"chunk-1"}


@pytest.mark.asyncio
async def test_caches_empty_text_without_treating_it_as_miss() -> None:
    client = FakeRedis()
    cache = build_cache(client)

    await cache.set_text("chunk-1", "", document_id=7)

    assert await cache.get_text("chunk-1") == ""
    assert await cache.get_text("missing-chunk") is None


@pytest.mark.asyncio
async def test_invalidates_only_the_target_document() -> None:
    client = FakeRedis()
    cache = build_cache(client)
    await cache.set_text("chunk-1", "甲", document_id=7)
    await cache.set_text("chunk-2", "乙", document_id=7)
    await cache.set_text("chunk-3", "丙", document_id=8)

    await cache.invalidate_document(7)

    assert await cache.get_text("chunk-1") is None
    assert await cache.get_text("chunk-2") is None
    assert await cache.get_text("chunk-3") == "丙"


@pytest.mark.asyncio
async def test_invalidate_chunk_removes_single_key() -> None:
    client = FakeRedis()
    cache = build_cache(client)
    await cache.set_text("chunk-1", "甲", document_id=7)

    await cache.invalidate_chunk("chunk-1")

    assert await cache.get_text("chunk-1") is None


@pytest.mark.asyncio
async def test_degrades_silently_when_redis_is_unavailable() -> None:
    cache = build_cache(FakeRedis(fail=True))

    assert await cache.get_text("chunk-1") is None
    await cache.set_text("chunk-1", "正文", document_id=7)
    await cache.invalidate_document(7)
    await cache.invalidate_chunk("chunk-1")


@pytest.mark.asyncio
async def test_disabled_cache_never_touches_redis() -> None:
    client = FakeRedis()
    cache = build_cache(client, enabled=False)

    await cache.set_text("chunk-1", "正文", document_id=7)
    await cache.invalidate_document(7)

    assert await cache.get_text("chunk-1") is None
    assert client.values == {}
    assert client.sets == {}
