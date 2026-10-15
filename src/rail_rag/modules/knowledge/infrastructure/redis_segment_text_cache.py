"""知识分片正文的 Redis 缓存适配器：为父块回溯提供二级缓存并支持按文档失效。

定义 RedisSegmentTextCache；缓存只保存分片正文，不保存任何权限结论，
命中缓存的正文仍必须通过 MySQL 二次授权后才能进入检索上下文。
"""

from __future__ import annotations

import logging

from redis.asyncio import Redis
from redis.exceptions import RedisError

from rail_rag.config.settings import Settings


logger = logging.getLogger(__name__)


class RedisSegmentTextCache:
    """使用认证 Session 的 Redis 实例缓存分片正文，故障时静默退化为回源 MySQL。"""

    _KEY_PREFIX = "rail_rag:cache:segment:"
    _DOCUMENT_INDEX_PREFIX = "rail_rag:cache:segment:index:"

    def __init__(self, settings: Settings, *, client: Redis | None = None) -> None:
        password = (
            settings.redis_password.get_secret_value()
            if settings.redis_password is not None
            else None
        )
        self._enabled = settings.segment_cache_enabled
        self._ttl_seconds = settings.segment_cache_ttl_seconds
        self._client = client or Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            password=password or None,
            db=settings.redis_database,
            decode_responses=True,
            socket_connect_timeout=settings.redis_connect_timeout_seconds,
            socket_timeout=settings.redis_connect_timeout_seconds,
        )

    async def get_text(self, chunk_id: str) -> str | None:
        """读取缓存正文；未命中或 Redis 不可用时返回 None，由调用方回源 MySQL。"""
        if not self._enabled or not chunk_id:
            return None
        try:
            cached = await self._client.get(self._key(chunk_id))
        except RedisError:
            logger.warning(
                "Segment text cache read failed; falling back to MySQL.",
                extra={"error_type": "RedisError"},
            )
            return None
        if cached is None:
            return None
        return str(cached)

    async def set_text(
        self, chunk_id: str, text: str, *, document_id: int | None = None
    ) -> None:
        """写入正文缓存并维护按文档失效索引；空正文同样缓存以防击穿。"""
        if not self._enabled or not chunk_id:
            return
        try:
            await self._client.set(self._key(chunk_id), text, ex=self._ttl_seconds)
            if document_id is not None:
                index_key = self._document_index_key(document_id)
                await self._client.sadd(index_key, chunk_id)
                await self._client.expire(index_key, self._ttl_seconds)
        except RedisError:
            logger.warning(
                "Segment text cache write failed; continuing without cache.",
                extra={"error_type": "RedisError"},
            )

    async def invalidate_document(self, document_id: int) -> None:
        """按文档批量失效正文缓存，用于重新切片、重新向量化或文档失效。"""
        if not self._enabled:
            return
        index_key = self._document_index_key(document_id)
        try:
            chunk_ids = await self._client.smembers(index_key)
            if chunk_ids:
                await self._client.delete(
                    *[self._key(str(chunk_id)) for chunk_id in chunk_ids]
                )
            await self._client.delete(index_key)
        except RedisError:
            logger.warning(
                "Segment text cache invalidation failed; cached text expires by TTL.",
                extra={"error_type": "RedisError"},
            )

    async def invalidate_chunk(self, chunk_id: str) -> None:
        """失效单个分片正文缓存。"""
        if not self._enabled or not chunk_id:
            return
        try:
            await self._client.delete(self._key(chunk_id))
        except RedisError:
            logger.warning(
                "Segment text cache invalidation failed; cached text expires by TTL.",
                extra={"error_type": "RedisError"},
            )

    @classmethod
    def _key(cls, chunk_id: str) -> str:
        return f"{cls._KEY_PREFIX}{chunk_id}"

    @classmethod
    def _document_index_key(cls, document_id: int) -> str:
        return f"{cls._DOCUMENT_INDEX_PREFIX}{document_id}"
