"""全量评测总报告测试：验证 RAGAS 合并状态与中文 Markdown 渲染。

定义三项测试，覆盖完整运行、小样本试跑和未提供 RAGAS 报告时的总报告输出。
"""

from __future__ import annotations

import json
from pathlib import Path


def make_report() -> dict[str, object]:
    """构造与编排器一致的最小总报告。"""
    return {
        "mode": "mock",
        "datasets": {"mineru": 30, "p0": 20, "prompt": 12},
        "mineru_retrieval": {
            "summary": {
                "recall_at_5": 1.0,
                "recall_at_10": 1.0,
                "mrr": 0.9,
                "section_hit": 1.0,
                "latency_ms": 0.1,
            }
        },
        "prompt_comparison": {
            "metrics": {
                "overall_accuracy": {"old": 0.8, "current": 1.0},
                "related_accuracy": {"old": 1.0, "current": 1.0},
                "entity_exact": {"old": 0.5, "current": 0.9},
                "entity_micro_f1": {"old": 0.6, "current": 0.95},
            }
        },
    }


def write_ragas(path: Path, count: int) -> None:
    """写入一份最小 RAGAS 报告。"""
    payload = {
        "count": count,
        "generated_at": "2026-10-17T00:00:00+00:00",
        "evaluator": {"model": "deepseek-v4-flash-0731"},
        "summary": {
            "overall": {
                "context_precision": 0.9,
                "context_recall": 0.8,
                "faithfulness": 0.7,
                "answer_relevancy": 0.6,
                "factual_correctness": 0.5,
            },
            "by_category": {"owner_vehicle": {"context_precision": 0.9}},
        },
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_merge_ragas_marks_full_run_completed(load_script, tmp_path: Path) -> None:
    module = load_script("run_full_rag_evaluation.py")
    report = make_report()
    ragas_path = tmp_path / "ragas.json"
    write_ragas(ragas_path, count=20)

    module.merge_ragas(report, ragas_path)
    markdown = module.render_markdown(report)

    assert report["ragas"]["status"] == "COMPLETED"
    assert report["ragas"]["count"] == 20
    assert "RAGAS 真实评测" in markdown
    assert "0.9000" in markdown
    assert "小样本试跑" not in markdown


def test_merge_ragas_marks_small_run_smoke(load_script, tmp_path: Path) -> None:
    module = load_script("run_full_rag_evaluation.py")
    report = make_report()
    ragas_path = tmp_path / "ragas-smoke.json"
    write_ragas(ragas_path, count=5)

    module.merge_ragas(report, ragas_path)
    markdown = module.render_markdown(report)

    assert report["ragas"]["status"] == "SMOKE"
    assert "小样本试跑" in markdown
    assert "5 / 20" in markdown


def test_render_markdown_without_ragas_lists_next_commands(load_script) -> None:
    module = load_script("run_full_rag_evaluation.py")
    report = make_report()

    module.merge_ragas(report, None)
    markdown = module.render_markdown(report)

    assert report["ragas"]["status"] == "PENDING"
    assert "本次运行尚未包含真实 RAGAS 评分" in markdown
    assert "collect_live_rag_results.py" in markdown
    assert "MinerU 文档检索" in markdown
    assert "意图 Prompt 对比" in markdown
    assert "0.9000" in markdown
