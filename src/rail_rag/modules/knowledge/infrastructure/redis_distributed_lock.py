"""Redis 分布式锁：提供非阻塞获取、自动续租和安全释放能力。

定义 DistributedLockNotAcquiredError 与 RedisDistributedLock；上传、分片和向量化
流程使用不同 scene/key 组合互斥，获取失败会立即抛出异常而不等待。
"""

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator
from uuid import uuid4

from redis.asyncio import Redis
from redis.exceptions import RedisError

from rail_rag.config.settings import Settings


class DistributedLockNotAcquiredError(RuntimeError):
    """非阻塞分布式锁已被其他任务持有时抛出。"""


class DistributedLockBackendError(RuntimeError):
    """Redis 无法提供分布式锁服务时抛出。"""


class RedisDistributedLock:
    """带看门狗续租任务、基于随机令牌校验的 Redis 锁。"""

    _RELEASE_SCRIPT = """
        if redis.call('get', KEYS[1]) == ARGV[1] then
            return redis.call('del', KEYS[1])
        end
        return 0
    """
    _RENEW_SCRIPT = """
        if redis.call('get', KEYS[1]) == ARGV[1] then
            return redis.call('pexpire', KEYS[1], ARGV[2])
        end
        return 0
    """

    def __init__(self, settings: Settings, *, client: Redis | None = None) -> None:
        password = (
            settings.redis_password.get_secret_value()
            if settings.redis_password is not None
            else None
        )
        self._lease_seconds = settings.redis_lock_lease_seconds
        self._client = client or Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            password=password or None,
            db=settings.redis_database,
            decode_responses=True,
            socket_connect_timeout=settings.redis_connect_timeout_seconds,
            socket_timeout=settings.redis_connect_timeout_seconds,
        )

    @asynccontextmanager
    async def acquire(
        self, scene: str, key: str | int, *, wait_time_seconds: float = 0
    ) -> AsyncIterator[None]:
        if wait_time_seconds != 0:
            raise ValueError("Only non-blocking distributed locks are supported")
        # scene 隔离业务场景，key 锁定具体对象；随机 token 防止旧任务误释放新任务的锁。
        lock_key = f"rail_rag:lock:{scene}:{key}"
        token = str(uuid4())
        try:
            acquired = await self._client.set(
                lock_key, token, nx=True, ex=self._lease_seconds
            )
        except RedisError as exc:
            raise DistributedLockBackendError("Redis distributed lock is unavailable") from exc
        if not acquired:
            raise DistributedLockNotAcquiredError(
                f"Lock is already held for scene={scene}, key={key}"
            )

        # 锁持有时间可能超过初始租期，后台协程会按租期的三分之一周期续租。
        renewal_task = asyncio.create_task(self._renew(lock_key, token))
        try:
            yield
        finally:
            renewal_task.cancel()
            try:
                await renewal_task
            except asyncio.CancelledError:
                pass
            except RedisError:
                # 续租任务异常后仍尝试显式释放；租期自然过期是最后的兜底机制。
                pass
            try:
                # Lua 脚本先比对 token；只有当前持锁者才能删除该键。
                await self._client.eval(self._RELEASE_SCRIPT, 1, lock_key, token)
            except RedisError as exc:
                raise DistributedLockBackendError(
                    "Redis distributed lock is unavailable"
                ) from exc

    async def _renew(self, lock_key: str, token: str) -> None:
        interval = max(1, self._lease_seconds // 3)
        while True:
            await asyncio.sleep(interval)
            renewed = await self._client.eval(
                self._RENEW_SCRIPT,
                1,
                lock_key,
                token,
                self._lease_seconds * 1000,
            )
            if not renewed:
                return
