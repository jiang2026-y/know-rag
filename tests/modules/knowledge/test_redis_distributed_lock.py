"""验证 Redis 非阻塞分布式锁的互斥与安全释放。"""

from contextlib import asynccontextmanager

import pytest

from rail_rag.config.settings import Settings
from rail_rag.modules.knowledge.infrastructure.redis_distributed_lock import (
    DistributedLockNotAcquiredError,
    RedisDistributedLock,
)


class FakeRedis:
    """用内存模拟本测试需要的 Redis SET 和 Lua 原子操作。"""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def set(self, name: str, value: str, *, nx: bool, ex: int) -> bool:
        assert nx is True
        assert ex > 0
        if name in self.values:
            return False
        self.values[name] = value
        return True

    async def eval(self, script: str, _: int, key: str, *args: object) -> int:
        token = str(args[0])
        if self.values.get(key) != token:
            return 0
        if "del" in script:
            del self.values[key]
        return 1


@pytest.mark.asyncio
async def test_lock_is_non_blocking_and_released_after_scope() -> None:
    redis = FakeRedis()
    lock = RedisDistributedLock(
        Settings(redis_lock_lease_seconds=3), client=redis  # type: ignore[arg-type]
    )

    async with lock.acquire("document-upload", "user-1", wait_time_seconds=0):
        with pytest.raises(DistributedLockNotAcquiredError):
            async with lock.acquire("document-upload", "user-1", wait_time_seconds=0):
                pass

    async with lock.acquire("document-upload", "user-1", wait_time_seconds=0):
        pass


@pytest.mark.asyncio
async def test_lock_rejects_a_wait_time_other_than_zero() -> None:
    lock = RedisDistributedLock(
        Settings(redis_lock_lease_seconds=3), client=FakeRedis()  # type: ignore[arg-type]
    )

    with pytest.raises(ValueError, match="non-blocking"):
        async with lock.acquire("document-split", 1, wait_time_seconds=1):
            pass
