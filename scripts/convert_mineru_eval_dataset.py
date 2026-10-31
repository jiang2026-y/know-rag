"""将同源 MinerU 检索样本转换为当前项目 RAGAS 评测契约。

定义 load_rows、convert_row、convert_rows 和 main；脚本只读取已有 MinerU JSONL，
校验 30 条样本的参考答案与金标准上下文，并写出可供真实链路采集和 RAGAS 评分使用的 JSONL。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


EXPECTED_COUNT = 30


def load_rows(path: Path) -> list[dict[str, Any]]:
    """读取并校验 MinerU 原始评测样本。"""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict) or not isinstance(value.get("id"), str):
            raise ValueError(f"第 {line_number} 行缺少字符串 id")
        if value["id"] in seen:
            raise ValueError(f"样本 id 重复：{value['id']}")
        seen.add(value["id"])
        for field in ("question", "reference_answer", "gold_context", "section"):
            if not isinstance(value.get(field), str) or not value[field].strip():
                raise ValueError(f"{value['id']} 缺少非空字段 {field}")
        rows.append(value)
    if len(rows) != EXPECTED_COUNT:
        raise ValueError(f"MinerU 样本数应为 {EXPECTED_COUNT}，实际为 {len(rows)}")
    return rows


def convert_row(row: dict[str, Any]) -> dict[str, Any]:
    """把一条 MinerU 样本映射为 RAGAS 和真实采集共用的字段。"""
    answer = str(row["reference_answer"]).strip()
    context = str(row["gold_context"]).strip()
    return {
        "id": row["id"],
        "category": "mineru_manual",
        "question": str(row["question"]).strip(),
        "ground_truths": [answer],
        "contexts": [context],
        "answer": answer,
        "reference_answer": answer,
        "gold_context": context,
        "section": row["section"],
        "source_document": row.get("source_document"),
        "generation_mode": row.get("generation_mode", "mock"),
        "dataset_kind": "mineru",
        "principal_type": "CUSTOMER",
        "expected_related": True,
        "expected_intent": "车辆使用与技术指导",
        "expected_strategies": ["knowledge_base"],
        "expected_source_types": ["knowledge_base"],
    }


def convert_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """转换整份 MinerU 样本并保留原始顺序。"""
    return [convert_row(row) for row in rows]


def main() -> None:
    """解析路径、执行转换并写出 UTF-8 JSONL。"""
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="转换 MinerU RAGAS 评测数据")
    parser.add_argument(
        "--input",
        type=Path,
        default=root / "evals" / "datasets" / "mineru_r7_eval.jsonl",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=root / "evals" / "datasets" / "mineru_ragas.jsonl",
    )
    args = parser.parse_args()
    rows = convert_rows(load_rows(args.input))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    print(f"已转换 MinerU RAGAS 样本：{len(rows)} 条；输出：{args.output}")


if __name__ == "__main__":
    main()
