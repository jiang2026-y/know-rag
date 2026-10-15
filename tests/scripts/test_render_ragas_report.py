"""RAGAS 报告渲染测试：验证中文指标、缺失分数与异常样本展示。

定义两项测试，覆盖完整报告渲染和空报告渲染，确保缺失分数显示为 N/A 而不是 0。
"""

from __future__ import annotations


def test_render_report_contains_metrics_categories_and_failures(load_script) -> None:
    module = load_script("render_ragas_report.py")
    report = {
        "generated_at": "2026-10-17T00:00:00+00:00",
        "dataset": "evals/datasets/p0_baseline.jsonl",
        "runtime_results": "evals/reports/live/runtime-results.jsonl",
        "count": 2,
        "evaluator": {"model": "deepseek-v4-flash-0731", "embedding_model": "text-embedding-v4"},
        "summary": {
            "overall": {
                "context_precision": 0.5,
                "context_recall": None,
                "faithfulness": 0.8,
            },
            "by_category": {"owner_vehicle": {"context_precision": 0.5}},
        },
        "results": [
            {
                "id": "p0-001",
                "status": "OK",
                "scores": {"context_precision": 0.5, "faithfulness": 0.8},
                "runtime_result_used": True,
            },
            {
                "id": "p0-002",
                "status": "PARTIAL",
                "scores": {"context_precision": None},
                "errors": {"context_precision": "TimeoutError: judge timeout"},
                "runtime_result_used": True,
            },
        ],
    }

    markdown = module.render_report(report)

    assert "# RAGAS 评测报告" in markdown
    assert "上下文精确率" in markdown and "0.5000" in markdown
    assert "N/A" in markdown
    assert "owner_vehicle" in markdown
    assert "`p0-002`" in markdown
    assert "TimeoutError" in markdown
    assert "非汽车问题样本会被跳过" in markdown


def test_render_report_without_scores_marks_na(load_script) -> None:
    module = load_script("render_ragas_report.py")

    markdown = module.render_report(
        {"count": 0, "summary": {"overall": {}, "by_category": {}}, "results": []}
    )

    assert "N/A（本次运行没有产生可用分数）" in markdown
    assert "没有逐条结果" in markdown
    assert "本次运行没有指标调用异常" in markdown
