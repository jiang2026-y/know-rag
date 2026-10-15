"""RAGAS 报告渲染脚本：把 JSON 评分结果转换为中文 Markdown 报告。

定义 METRIC_LABELS、load_report、format_score、render_report 和 main；脚本只读取评分 JSON
并写出 Markdown，不调用模型、不修改评测数据，缺少分数时明确标注而不是按 0 分处理。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


METRIC_LABELS = {
    "context_precision": "上下文精确率",
    "context_recall": "上下文召回率",
    "faithfulness": "忠实度",
    "answer_relevancy": "答案相关性",
    "factual_correctness": "事实正确性",
}


def load_report(path: Path) -> dict[str, Any]:
    """读取 RAGAS JSON 报告。"""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("RAGAS 报告必须是 JSON 对象")
    return payload


def format_score(value: Any) -> str:
    """格式化分数；缺失值显示为 N/A，避免把未评分当成 0 分。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "N/A"
    return f"{float(value):.4f}"


def _metric_names(report: dict[str, Any]) -> list[str]:
    """优先使用报告里出现的指标顺序，缺省回退到五项固定指标。"""
    overall = report.get("summary", {}).get("overall", {})
    if isinstance(overall, dict) and overall:
        return list(overall.keys())
    return list(METRIC_LABELS)


def _error_summary(errors: dict[str, Any], limit: int = 160) -> str:
    """把逐指标错误压缩成一行，便于在表格中展示。"""
    parts: list[str] = []
    for name, message in errors.items():
        text = str(message).replace("\n", " ").strip()
        if len(text) > limit:
            text = text[:limit] + "…"
        parts.append(f"{METRIC_LABELS.get(name, name)}：{text}")
    return "；".join(parts)


def render_report(report: dict[str, Any], *, title: str = "RAGAS 评测报告") -> str:
    """把评分报告渲染成中文 Markdown。"""
    metrics = _metric_names(report)
    evaluator = report.get("evaluator", {}) if isinstance(report.get("evaluator"), dict) else {}
    lines: list[str] = [f"# {title}", ""]
    lines.extend(
        [
            "## 运行信息",
            "",
            "| 项目 | 值 |",
            "| --- | --- |",
            f"| 生成时间 | {report.get('generated_at') or '未知'} |",
            f"| 数据集 | `{report.get('dataset') or '未知'}` |",
            f"| 运行结果 | `{report.get('runtime_results') or '未使用（评估参考答案）'}` |",
            f"| 样本数 | {report.get('count', 0)} |",
            f"| 评审模型 | `{evaluator.get('model') or '未知'}` |",
            f"| 向量模型 | `{evaluator.get('embedding_model') or '未知'}` |",
            "",
        ]
    )

    overall = report.get("summary", {}).get("overall", {})
    lines.extend(["## 总体指标", "", "| 指标 | 分数 |", "| --- | --- |"])
    if isinstance(overall, dict) and overall:
        for name in metrics:
            lines.append(f"| {METRIC_LABELS.get(name, name)} | {format_score(overall.get(name))} |")
    else:
        lines.append("| 全部指标 | N/A（本次运行没有产生可用分数） |")
    lines.append("")

    by_category = report.get("summary", {}).get("by_category", {})
    lines.extend(["## 分类指标", ""])
    if isinstance(by_category, dict) and by_category:
        header = "| 类别 | " + " | ".join(
            METRIC_LABELS.get(name, name) for name in metrics
        ) + " |"
        divider = "| --- | " + " | ".join(["---"] * len(metrics)) + " |"
        lines.extend([header, divider])
        for category, values in by_category.items():
            row = [str(category)]
            for name in metrics:
                row.append(
                    format_score(values.get(name) if isinstance(values, dict) else None)
                )
            lines.append("| " + " | ".join(row) + " |")
    else:
        lines.append("没有可用的分类分数。")
    lines.extend(["", "## 逐条结果", ""])

    results = report.get("results", [])
    if isinstance(results, list) and results:
        header = "| 样本 | 状态 | " + " | ".join(
            METRIC_LABELS.get(name, name) for name in metrics
        ) + " | 运行结果 |"
        divider = "| --- | --- | " + " | ".join(["---"] * len(metrics)) + " | --- |"
        lines.extend([header, divider])
        for item in results:
            if not isinstance(item, dict):
                continue
            scores = item.get("scores", {}) if isinstance(item.get("scores"), dict) else {}
            row = [
                f"`{item.get('id', '?')}`",
                str(item.get("status", "")),
                *[format_score(scores.get(name)) for name in metrics],
                "是" if item.get("runtime_result_used") else "否",
            ]
            lines.append("| " + " | ".join(row) + " |")
    else:
        lines.append("没有逐条结果。")

    failures = [
        item
        for item in results
        if isinstance(item, dict) and isinstance(item.get("errors"), dict) and item["errors"]
    ]
    lines.extend(["", "## 异常样本", ""])
    if failures:
        for item in failures:
            lines.append(f"- `{item.get('id', '?')}`：{_error_summary(item['errors'])}")
    else:
        lines.append("本次运行没有指标调用异常。")

    lines.extend(
        [
            "",
            "## 说明",
            "",
            "- 分数由 RAGAS 0.4 指标与评审模型给出，属于模型评判结果，不等同于人工验收结论。",
            "- 非汽车问题样本会被跳过，不计入平均分；缺失指标显示为 N/A，不按 0 分处理。",
            "- 若报告是基于数据集自带参考上下文生成（`runtime_results` 为空），只能验证评分流程，不能代表线上召回质量。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    """解析参数并写出 Markdown 报告。"""
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="把 RAGAS JSON 报告渲染为中文 Markdown")
    parser.add_argument(
        "--input",
        type=Path,
        default=root / "evals" / "reports" / "live" / "ragas.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=root / "evals" / "reports" / "live" / "ragas-report.md",
    )
    parser.add_argument("--title", default="RAGAS 评测报告")
    args = parser.parse_args()
    report = load_report(args.input)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render_report(report, title=args.title), encoding="utf-8")
    print(f"已生成 RAGAS Markdown 报告：{args.output}")


if __name__ == "__main__":
    main()
