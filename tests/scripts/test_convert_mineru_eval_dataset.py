"""MinerU 评测数据转换测试：验证样本数量、RAGAS 字段和输入校验。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_convert_mineru_dataset_has_ragas_contract(load_script) -> None:
    """同源 30 条样本应映射为 RAGAS 和真实采集共用字段。"""
    module = load_script("convert_mineru_eval_dataset.py")
    root = Path(__file__).resolve().parents[2]
    rows = module.load_rows(root / "evals" / "datasets" / "mineru_r7_eval.jsonl")
    converted = module.convert_rows(rows)

    assert len(converted) == 30
    assert len({row["id"] for row in converted}) == 30
    assert all(row["ground_truths"] == [row["reference_answer"]] for row in converted)
    assert all(row["contexts"] == [row["gold_context"]] for row in converted)
    assert all(row["dataset_kind"] == "mineru" for row in converted)
    assert all(row["principal_type"] == "CUSTOMER" for row in converted)


def test_load_rows_rejects_wrong_count(load_script, tmp_path: Path) -> None:
    """输入不是完整 30 条同源样本时应拒绝生成结果。"""
    module = load_script("convert_mineru_eval_dataset.py")
    row = {
        "id": "mineru-001",
        "question": "问题",
        "reference_answer": "答案",
        "gold_context": "证据",
        "section": "章节",
    }
    path = tmp_path / "bad.jsonl"
    path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="样本数"):
        module.load_rows(path)
