"""全量 RAG 评测编排器：运行 MinerU、Prompt 和离线 RAG 评测并生成总报告。

定义 run_script、load_json、merge_ragas、format_value、render_markdown 和 main；
编排器可选合并真实 RAGAS 报告，并以 UTF-8 同时写出 JSON 与中文 Markdown 总报告。
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
from typing import Any


RAGAS_METRIC_LABELS = {
    "context_precision": "上下文精确率",
    "context_recall": "上下文召回率",
    "faithfulness": "忠实度",
    "answer_relevancy": "答案相关性",
    "factual_correctness": "事实正确性",
}


def run_script(root: Path, name: str, *extra: str) -> None:
    """以当前解释器执行子评测脚本，失败时立即中断。"""
    subprocess.run(
        [sys.executable, str(root / "scripts" / name), *extra], check=True
    )


def load_json(path: Path) -> dict[str, Any]:
    """读取 UTF-8 JSON 对象。"""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"报告必须是 JSON 对象：{path}")
    return payload


def merge_ragas(report: dict[str, Any], ragas_path: Path | None) -> None:
    """把真实 RAGAS 报告并入总报告；未提供时保留明确的待运行状态。"""
    if ragas_path is None:
        report["ragas"] = {
            "status": "PENDING",
            "note": (
                "先运行 scripts/collect_live_rag_results.py 采集真实链路结果，"
                "再用 --ragas-report 合并评分"
            ),
        }
        return
    payload = load_json(ragas_path)
    count = payload.get("count")
    expected = (
        report.get("datasets", {}).get("p0", 20)
        if isinstance(report.get("datasets"), dict)
        else 20
    )
    is_full_run = isinstance(count, int) and count >= int(expected)
    report["ragas"] = {
        "status": "COMPLETED" if is_full_run else "SMOKE",
        "count": count,
        "expected_count": expected,
        "generated_at": payload.get("generated_at"),
        "evaluator": payload.get("evaluator"),
        "summary": payload.get("summary", {}),
        "source": str(ragas_path),
    }


def format_value(value: Any) -> str:
    """统一格式化数值；缺失值显示为 N/A。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "N/A"
    return f"{float(value):.4f}"


def render_markdown(report: dict[str, Any]) -> str:
    """渲染中文 Markdown 总报告。"""
    datasets = report.get("datasets", {}) if isinstance(report.get("datasets"), dict) else {}
    mineru = (
        report.get("mineru_retrieval", {}).get("summary", {})
        if isinstance(report.get("mineru_retrieval"), dict)
        else {}
    )
    prompt = (
        report.get("prompt_comparison", {}).get("metrics", {})
        if isinstance(report.get("prompt_comparison"), dict)
        else {}
    )
    ragas = report.get("ragas", {}) if isinstance(report.get("ragas"), dict) else {}
    lines: list[str] = [
        "# 全量 RAG 评测报告",
        "",
        f"- 生成时间：{datetime.now(timezone.utc).isoformat()}",
        f"- 运行模式：`{report.get('mode', 'mock')}`",
        f"- 数据集规模：MinerU {datasets.get('mineru', 0)} 条、P0 {datasets.get('p0', 0)} 条、意图 Prompt {datasets.get('prompt', 0)} 条",
        "",
        "## 1. MinerU 文档检索",
        "",
        "| 指标 | 数值 |",
        "| --- | --- |",
        f"| Recall@5 | {format_value(mineru.get('recall_at_5'))} |",
        f"| Recall@10 | {format_value(mineru.get('recall_at_10'))} |",
        f"| MRR | {format_value(mineru.get('mrr'))} |",
        f"| 章节命中率 | {format_value(mineru.get('section_hit'))} |",
        f"| 平均耗时（ms） | {format_value(mineru.get('latency_ms'))} |",
        "",
        "## 2. 意图 Prompt 对比",
        "",
        "| 指标 | 旧提示词 | 当前提示词 |",
        "| --- | --- | --- |",
    ]
    for key, label in (
        ("overall_accuracy", "意图准确率"),
        ("related_accuracy", "相关性判断准确率"),
        ("entity_exact", "实体完全匹配率"),
        ("entity_micro_f1", "实体 Micro-F1"),
    ):
        values = prompt.get(key, {}) if isinstance(prompt.get(key), dict) else {}
        lines.append(
            f"| {label} | {format_value(values.get('old'))} | {format_value(values.get('current'))} |"
        )

    lines.extend(["", "## 3. RAGAS 真实评测", ""])
    if ragas.get("status") in {"COMPLETED", "SMOKE"}:
        summary = ragas.get("summary", {})
        overall = summary.get("overall", {}) if isinstance(summary, dict) else {}
        evaluator = ragas.get("evaluator", {}) if isinstance(ragas.get("evaluator"), dict) else {}
        lines.extend(
            [
                f"- 样本数：{ragas.get('count')} / {ragas.get('expected_count', 20)}；评审模型：`{evaluator.get('model') or '未知'}`",
                f"- 生成时间：{ragas.get('generated_at')}",
            ]
        )
        if ragas.get("status") == "SMOKE":
            lines.extend(
                [
                    "",
                    "> 注意：这是小样本试跑结果，仅用于验证链路与指标口径，不能代表 20 条完整评测。",
                    "> 完整评测请执行：`python scripts/collect_live_rag_results.py` 后再运行 RAGAS 并以 `--ragas-report` 合并。",
                ]
            )
        lines.extend(["", "| 指标 | 分数 |", "| --- | --- |"])
        for name, label in RAGAS_METRIC_LABELS.items():
            lines.append(f"| {label} | {format_value(overall.get(name))} |")
        by_category = summary.get("by_category", {}) if isinstance(summary, dict) else {}
        if isinstance(by_category, dict) and by_category:
            lines.extend(["", "| 类别 | " + " | ".join(RAGAS_METRIC_LABELS.values()) + " |", "| --- | " + " | ".join(["---"] * len(RAGAS_METRIC_LABELS)) + " |"])
            for category, values in by_category.items():
                row = [str(category)]
                row.extend(
                    format_value(values.get(name) if isinstance(values, dict) else None)
                    for name in RAGAS_METRIC_LABELS
                )
                lines.append("| " + " | ".join(row) + " |")
    else:
        lines.extend(
            [
                "本次运行尚未包含真实 RAGAS 评分。",
                "",
                "```powershell",
                "python scripts/collect_live_rag_results.py --limit 5",
                "python scripts/collect_live_rag_results.py",
                "python scripts/run_ragas_baseline.py --runtime-results evals/reports/live/runtime-results.jsonl --mode current --output evals/reports/live/ragas.json",
                "python scripts/render_ragas_report.py",
                "python scripts/run_full_rag_evaluation.py --output-dir evals/reports/full-rag --ragas-report evals/reports/live/ragas.json",
                "```",
            ]
        )
    lines.extend(
        [
            "",
            "## 4. 说明",
            "",
            "- MinerU 检索与意图 Prompt 对比默认使用 mock 适配器，只能验证数据流与指标口径；真实检索与回答以 RAGAS 章节为准。",
            "- RAGAS 分数由评审模型给出，不等同于人工验收；非汽车问题样本不参与平均分。",
            "- 报告中不包含 API Key、真实顾客、员工或车辆信息。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    """执行可恢复的本地评测准备流程并写出总报告。"""
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--mode", choices=["mock", "api"], default="mock")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--ragas-report",
        type=Path,
        help="可选的真实 RAGAS JSON 报告，用于合并到总报告",
    )
    args = parser.parse_args()

    out = args.output_dir
    out.mkdir(parents=True, exist_ok=True)
    dataset = root / "evals/datasets/mineru_r7_eval.jsonl"
    document = root / "evals/fixtures/MinerU_markdown_r7-product-manual-for-testset.md"
    run_script(
        root,
        "prepare_full_rag_eval.py",
        "--mineru-document",
        str(document),
        "--mineru-count",
        "30",
        "--mode",
        "mock",
        "--output",
        str(dataset),
    )
    run_script(
        root,
        "run_mineru_retrieval_eval.py",
        "--dataset",
        str(dataset),
        "--mode",
        "mock",
        "--output",
        str(out / "mineru-retrieval.json"),
    )
    run_script(
        root,
        "run_prompt_comparison.py",
        "--dataset",
        str(root / "evals/datasets/intent_comparison.jsonl"),
        "--mode",
        "mock",
        "--output",
        str(out / "intent-comparison.json"),
    )
    run_script(
        root,
        "run_rag_offline_eval.py",
        "--dataset",
        str(root / "evals/datasets/p0_baseline.jsonl"),
        "--mode",
        "mock",
        "--output",
        str(out / "runtime-results.jsonl"),
    )

    report: dict[str, Any] = {
        "mode": args.mode,
        "batch_size": args.batch_size,
        "datasets": {"mineru": 30, "p0": 20, "prompt": 12},
        "mineru_retrieval": load_json(out / "mineru-retrieval.json"),
        "prompt_comparison": load_json(out / "intent-comparison.json"),
    }
    merge_ragas(report, args.ragas_report)
    (out / "full-rag-evaluation.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out / "full-rag-evaluation.md").write_text(
        render_markdown(report), encoding="utf-8"
    )
    print(f"已生成总报告：{out}")


if __name__ == "__main__":
    main()
