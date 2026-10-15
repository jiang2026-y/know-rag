"""文档切片参数领域模型：定义五种切片策略、参数校验与持久化载荷。

定义 SplitType、InvalidSplitParamError、DocumentSplitParam、DEFAULT_SPLIT_PARAM 以及
parse_split_param，供上传接口、任务状态机和切片工厂共同校验并传递切片参数。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal


SplitType = Literal["TITLE", "LENGTH", "SMART", "REGEX", "SEPARATOR"]

SPLIT_TYPES: tuple[SplitType, ...] = ("TITLE", "LENGTH", "SMART", "REGEX", "SEPARATOR")

MIN_CHUNK_SIZE = 1
MAX_CHUNK_SIZE = 8000
DEFAULT_CHUNK_SIZE = 1000
DEFAULT_OVERLAP = 100
MIN_TITLE_LEVEL = 1
MAX_TITLE_LEVEL = 6
DEFAULT_TITLE_LEVEL = 6
MAX_PATTERN_LENGTH = 200


class InvalidSplitParamError(ValueError):
    """切片参数缺失、越界或格式非法时抛出，由接口层转换为 422。"""


@dataclass(frozen=True)
class DocumentSplitParam:
    """一次切片所使用的、已校验的策略与参数。"""

    split_type: SplitType = "TITLE"
    chunk_size: int = DEFAULT_CHUNK_SIZE
    overlap: int = DEFAULT_OVERLAP
    title_level: int = DEFAULT_TITLE_LEVEL
    separator: str | None = None
    regex: str | None = None

    def to_payload(self) -> dict[str, Any]:
        """转换为可直接写入 JSON 列的持久化载荷。"""
        return {
            "split_type": self.split_type,
            "chunk_size": self.chunk_size,
            "overlap": self.overlap,
            "title_level": self.title_level,
            "separator": self.separator,
            "regex": self.regex,
        }


DEFAULT_SPLIT_PARAM = DocumentSplitParam()


def parse_split_param(raw: Mapping[str, Any] | None) -> DocumentSplitParam:
    """校验并规范化切片参数；空值表示使用与现状一致的 TITLE 默认参数。"""
    if raw is None:
        return DEFAULT_SPLIT_PARAM

    split_type = _parse_split_type(raw.get("split_type"))
    chunk_size = _parse_int(raw.get("chunk_size"), "chunk_size", DEFAULT_CHUNK_SIZE)
    if not MIN_CHUNK_SIZE <= chunk_size <= MAX_CHUNK_SIZE:
        raise InvalidSplitParamError(
            f"chunk_size must be between {MIN_CHUNK_SIZE} and {MAX_CHUNK_SIZE}"
        )

    # 未显式指定重叠时按 chunk_size 收敛默认值，避免小分片被默认重叠判为非法参数。
    default_overlap = min(DEFAULT_OVERLAP, chunk_size - 1)
    overlap = _parse_int(raw.get("overlap"), "overlap", default_overlap)
    if not 0 <= overlap < chunk_size:
        raise InvalidSplitParamError("overlap must be at least 0 and smaller than chunk_size")

    title_level = _parse_int(raw.get("title_level"), "title_level", DEFAULT_TITLE_LEVEL)
    if not MIN_TITLE_LEVEL <= title_level <= MAX_TITLE_LEVEL:
        raise InvalidSplitParamError(
            f"title_level must be between {MIN_TITLE_LEVEL} and {MAX_TITLE_LEVEL}"
        )

    separator = _parse_pattern(raw.get("separator"), "separator")
    regex = _parse_pattern(raw.get("regex"), "regex")
    if separator is not None and regex is not None:
        raise InvalidSplitParamError("separator and regex are mutually exclusive")
    if split_type == "SEPARATOR" and separator is None:
        raise InvalidSplitParamError("SEPARATOR split requires a separator")
    if split_type == "REGEX":
        if regex is None:
            raise InvalidSplitParamError("REGEX split requires a regex")
        try:
            re.compile(regex)
        except re.error as exc:
            raise InvalidSplitParamError(f"regex is not a valid pattern: {exc}") from exc
    else:
        separator = separator if split_type == "SEPARATOR" else None
        regex = None

    if split_type == "SMART":
        overlap = chunk_size // 10

    return DocumentSplitParam(
        split_type=split_type,
        chunk_size=chunk_size,
        overlap=overlap,
        title_level=title_level,
        separator=separator,
        regex=regex,
    )


def _parse_split_type(value: object) -> SplitType:
    if value is None:
        return "TITLE"
    if not isinstance(value, str):
        raise InvalidSplitParamError("split_type must be a string")
    normalized = value.strip().upper()
    if not normalized:
        return "TITLE"
    if normalized not in SPLIT_TYPES:
        raise InvalidSplitParamError(
            f"split_type must be one of {', '.join(SPLIT_TYPES)}"
        )
    return normalized  # type: ignore[return-value]


def _parse_int(value: object, field: str, default: int) -> int:
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    if isinstance(value, bool):
        raise InvalidSplitParamError(f"{field} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError as exc:
            raise InvalidSplitParamError(f"{field} must be an integer") from exc
    raise InvalidSplitParamError(f"{field} must be an integer")


def _parse_pattern(value: object, field: str) -> str | None:
    """校验正则或分隔符原文；保留空白字符，只拒绝空串与超长输入。"""
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidSplitParamError(f"{field} must be a string")
    if not value:
        return None
    if len(value) > MAX_PATTERN_LENGTH:
        raise InvalidSplitParamError(
            f"{field} must not exceed {MAX_PATTERN_LENGTH} characters"
        )
    return value
