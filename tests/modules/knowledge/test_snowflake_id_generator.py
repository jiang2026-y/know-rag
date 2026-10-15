"""验证雪花分段 ID 的编码、并发序列与时钟回拨保护。"""

import pytest

from rail_rag.modules.knowledge.domain.snowflake_id_generator import (
    SnowflakeClockMovedBackwardsError,
    SnowflakeIdGenerator,
)


def test_encodes_timestamp_worker_and_sequence() -> None:
    now = SnowflakeIdGenerator.EPOCH_MILLISECONDS + 123
    generator = SnowflakeIdGenerator(7, clock_milliseconds=lambda: now)

    first_id = generator.next_id()
    second_id = generator.next_id()

    assert first_id == (123 << 22) | (7 << 12)
    assert second_id == first_id + 1
    assert generator.next_id_str() == str(second_id + 1)


def test_rejects_invalid_worker_id() -> None:
    with pytest.raises(ValueError, match="worker_id"):
        SnowflakeIdGenerator(1024)


def test_rejects_clock_rollback() -> None:
    timestamps = iter(
        [
            SnowflakeIdGenerator.EPOCH_MILLISECONDS + 10,
            SnowflakeIdGenerator.EPOCH_MILLISECONDS + 9,
        ]
    )
    generator = SnowflakeIdGenerator(0, clock_milliseconds=lambda: next(timestamps))

    generator.next_id()
    with pytest.raises(SnowflakeClockMovedBackwardsError, match="Clock moved backwards"):
        generator.next_id()
