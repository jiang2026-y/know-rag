"""P0 评测集校验脚本：检查 JSONL 结构、字段完整性、ID 唯一性和非汽车数据边界。

定义 load_rows、validate_row 和 main；脚本只读取评测文件，不连接数据库、不调用模型，也不修改项目数据。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


REQUIRED_FIELDS = {
    "id",
    "category",
    "principal_type",
    "question",
    "ground_truths",
    "contexts",
    "answer",
    "expected_related",
    "expected_intent",
    "expected_strategies",
    "expected_source_types",
}


def load_rows(path: Path) -> list[dict[str, Any]]:
    """读取并解析 UTF-8 JSONL 评测记录。"""
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"第 {line_number} 行不是合法 JSON：{exc}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"第 {line_number} 行必须是 JSON 对象")
        rows.append(row)
    return rows


def validate_row(row: dict[str, Any]) -> None:
    """校验单条评测记录的字段类型和安全边界。"""
    missing = REQUIRED_FIELDS - row.keys()
    if missing:
        raise ValueError(f"{row.get('id', '<unknown>')} 缺少字段：{sorted(missing)}")
    if not isinstance(row["id"], str) or not row["id"].strip():
        raise ValueError("id 必须是非空字符串")
    for field in ("ground_truths", "contexts", "expected_strategies", "expected_source_types"):
        if not isinstance(row[field], list) or any(not isinstance(item, str) for item in row[field]):
            raise ValueError(f"{row['id']} 的 {field} 必须是字符串数组")
    if row["expected_related"] is False:
        if row["expected_strategies"] or row["expected_source_types"] or row["contexts"]:
            raise ValueError(f"非汽车问题 {row['id']} 不得声明数据源或上下文")
        if row["expected_intent"] != "闲聊与通用问答":
            raise ValueError(f"非汽车问题 {row['id']} 必须使用闲聊与通用问答意图")
    else:
        if not row["expected_strategies"]:
            raise ValueError(f"汽车问题 {row['id']} 必须声明预期路由")


def main() -> None:
    """解析路径并输出校验结果。"""
    parser = argparse.ArgumentParser(description="校验 P0 RAG 脱敏评测集")
    parser.add_argument(
        "--path",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "evals" / "datasets" / "p0_baseline.jsonl",
    )
    args = parser.parse_args()
    rows = load_rows(args.path)
    if len({row["id"] for row in rows}) != len(rows):
        raise ValueError("评测集存在重复 id")
    for row in rows:
        validate_row(row)
    print(f"评测集校验通过：{len(rows)} 条；文件：{args.path}")


if __name__ == "__main__":
    main()
