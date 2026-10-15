"""雪花分段 ID 生成器：定义时钟回拨异常、线程安全的 ID 生成器及其实例缓存函数。"""

from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache
from threading import Lock
from time import time_ns


class SnowflakeClockMovedBackwardsError(RuntimeError):
    """系统时钟回拨时拒绝生成可能重复的雪花 ID。"""


class SnowflakeIdGenerator:
    """以固定 Worker ID 生成与 Java 模块兼容的 64 位雪花 ID。"""

    EPOCH_MILLISECONDS = 1_704_038_400_000  # 2024-01-01T00:00:00Z
    _WORKER_ID_BITS = 10
    _SEQUENCE_BITS = 12
    _MAX_WORKER_ID = (1 << _WORKER_ID_BITS) - 1
    _MAX_SEQUENCE = (1 << _SEQUENCE_BITS) - 1
    _TIMESTAMP_SHIFT = _WORKER_ID_BITS + _SEQUENCE_BITS
    _WORKER_ID_SHIFT = _SEQUENCE_BITS

    def __init__(
        self, worker_id: int, *, clock_milliseconds: Callable[[], int] | None = None
    ) -> None:
        if not 0 <= worker_id <= self._MAX_WORKER_ID:
            raise ValueError(
                f"worker_id must be between 0 and {self._MAX_WORKER_ID}"
            )
        self._worker_id = worker_id
        self._clock_milliseconds = clock_milliseconds or _current_milliseconds
        self._lock = Lock()
        self._last_timestamp = -1
        self._sequence = 0

    def next_id(self) -> int:
        """返回下一个全局唯一且时间有序的整数 ID。"""
        with self._lock:
            timestamp = self._clock_milliseconds()
            if timestamp < self._last_timestamp:
                raise SnowflakeClockMovedBackwardsError(
                    "Clock moved backwards; refusing to generate a snowflake ID for "
                    f"{self._last_timestamp - timestamp} milliseconds"
                )
            if timestamp == self._last_timestamp:
                self._sequence = (self._sequence + 1) & self._MAX_SEQUENCE
                if self._sequence == 0:
                    timestamp = self._wait_for_next_millisecond(timestamp)
            else:
                self._sequence = 0

            self._last_timestamp = timestamp
            return (
                ((timestamp - self.EPOCH_MILLISECONDS) << self._TIMESTAMP_SHIFT)
                | (self._worker_id << self._WORKER_ID_SHIFT)
                | self._sequence
            )

    def next_id_str(self) -> str:
        """返回适用于现有 VARCHAR 分段字段的十进制字符串 ID。"""
        return str(self.next_id())

    def _wait_for_next_millisecond(self, last_timestamp: int) -> int:
        timestamp = self._clock_milliseconds()
        while timestamp <= last_timestamp:
            timestamp = self._clock_milliseconds()
        return timestamp


def _current_milliseconds() -> int:
    return time_ns() // 1_000_000


@lru_cache
def get_snowflake_id_generator(worker_id: int = 0) -> SnowflakeIdGenerator:
    """返回当前进程中指定 Worker ID 对应的共享生成器。"""
    return SnowflakeIdGenerator(worker_id)
