"""RAGAS 基线评估脚本：使用百炼 DeepSeek-V4-Flash-0731 评审脱敏 P0 问答样本。

定义 load_samples、load_runtime_results、build_metrics、metric_inputs、score_sample 和 main；脚本默认评估
数据集中的人工参考上下文与答案，也支持通过 --runtime-results 传入当前应用真实召回和回答结果。
脚本只读取脱敏评测文件并写入评测报告，不修改业务数据库和线上会话。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import types
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rail_rag.config.settings import Settings


def load_samples(path: Path) -> list[dict[str, Any]]:
    """读取人工编写的 P0 JSONL 评测样本。"""
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError("评测样本必须是 JSON 对象")
            rows.append(value)
    return rows


def load_runtime_results(path: Path | None) -> dict[str, dict[str, Any]]:
    """读取可选的真实运行结果，按样本 ID 建立覆盖映射。"""
    if path is None:
        return {}
    results: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            value = json.loads(line)
            if not isinstance(value, dict) or not isinstance(value.get("id"), str):
                raise ValueError("运行结果必须包含字符串 id")
            key = f"{value['id']}::{value.get('mode', 'current')}"
            results[key] = value
    return results


def _install_ragas_langchain_compatibility() -> None:
    """为新版 LangChain 社区包补充 RAGAS 仅用于类型判断的 VertexAI 占位类。"""
    try:
        import langchain_community.chat_models.vertexai  # type: ignore[import-not-found]
    except ImportError:
        module_name = "langchain_community.chat_models.vertexai"
        module = types.ModuleType(module_name)
        module.ChatVertexAI = type("ChatVertexAI", (), {})
        sys.modules[module_name] = module

    from langchain_community import llms as community_llms

    if not hasattr(community_llms, "VertexAI"):
        community_llms.VertexAI = type("VertexAI", (), {})


def _dependencies() -> tuple[Any, ...]:
    """延迟加载评估依赖，使主项目不安装评测额外依赖时仍可启动。"""
    try:
        from openai import AsyncOpenAI
        _install_ragas_langchain_compatibility()
        from ragas.embeddings.base import embedding_factory
        from ragas.llms import llm_factory
        from ragas.metrics.collections import (
            AnswerRelevancy,
            ContextPrecision,
            ContextRecall,
            Faithfulness,
            FactualCorrectness,
        )
    except ImportError as exc:
        raise RuntimeError(
            '缺少评测依赖，请先执行：python -m pip install -e ".[eval]"'
        ) from exc
    return (
        AsyncOpenAI,
        embedding_factory,
        llm_factory,
        AnswerRelevancy,
        ContextPrecision,
        ContextRecall,
        Faithfulness,
        FactualCorrectness,
    )


def build_metrics(settings: Settings) -> dict[str, Any]:
    """构造 DeepSeek 评审 LLM、text-embedding-v4 评审 Embedding 和五项核心指标。"""
    (
        AsyncOpenAI,
        embedding_factory,
        llm_factory,
        AnswerRelevancy,
        ContextPrecision,
        ContextRecall,
        Faithfulness,
        FactualCorrectness,
    ) = _dependencies()
    if settings.embedding_api_key is None:
        raise RuntimeError("EMBEDDING_API_KEY 未配置，无法连接百炼评审模型和 text-embedding-v4")
    evaluator_client = AsyncOpenAI(
        api_key=settings.embedding_api_key.get_secret_value(),
        base_url=str(settings.embedding_base_url).rstrip("/"),
        timeout=settings.ragas_evaluator_response_timeout_seconds,
    )
    embedding_client = AsyncOpenAI(
        api_key=settings.embedding_api_key.get_secret_value(),
        base_url=str(settings.embedding_base_url).rstrip("/"),
        timeout=settings.embedding_response_timeout_seconds,
    )
    evaluator_llm = llm_factory(
        settings.ragas_evaluator_model_name,
        client=evaluator_client,
        max_tokens=settings.ragas_evaluator_max_tokens,
    )
    evaluator_embeddings = embedding_factory(
        "openai", model=settings.embedding_model_name, client=embedding_client
    )
    return {
        "context_precision": ContextPrecision(llm=evaluator_llm),
        "context_recall": ContextRecall(llm=evaluator_llm),
        "faithfulness": Faithfulness(llm=evaluator_llm),
        "answer_relevancy": AnswerRelevancy(
            llm=evaluator_llm, embeddings=evaluator_embeddings
        ),
        "factual_correctness": FactualCorrectness(llm=evaluator_llm),
    }


async def score_sample(
    sample: dict[str, Any], runtime: dict[str, Any] | None, metrics: dict[str, Any]
) -> dict[str, Any]:
    """评估单条样本并返回逐指标分数；运行结果可覆盖参考上下文和回答。"""
    current = runtime or {}
    question = str(sample["question"])
    reference = "\n".join(str(value) for value in sample["ground_truths"])
    contexts = current.get("retrieved_contexts", sample["contexts"])
    response = current.get("response", sample["answer"])
    if not isinstance(contexts, list) or not all(isinstance(value, str) for value in contexts):
        raise ValueError(f"{sample['id']} 的 retrieved_contexts 必须是字符串数组")
    response = str(response)
    if not sample["expected_related"]:
        return {
            "id": sample["id"],
            "status": "SKIPPED",
            "reason": "非汽车问题不应携带企业检索上下文",
            "expected_strategies": sample["expected_strategies"],
        }
    scores: dict[str, float | None] = {}
    errors: dict[str, str] = {}
    for name, metric in metrics.items():
        try:
            result = await metric.ascore(
                **metric_inputs(
                    name,
                    user_input=question,
                    reference=reference,
                    retrieved_contexts=contexts,
                    response=response,
                )
            )
            scores[name] = float(result.value)
        except Exception as exc:
            scores[name] = None
            errors[name] = f"{type(exc).__name__}: {exc}"
    return {
        "id": sample["id"],
        "status": "OK" if not errors else "PARTIAL",
        "scores": scores,
        "errors": errors,
        "expected_strategies": sample["expected_strategies"],
        "expected_source_types": sample["expected_source_types"],
        "runtime_result_used": runtime is not None,
    }


def metric_inputs(
    metric_name: str,
    *,
    user_input: str,
    reference: str,
    retrieved_contexts: list[str],
    response: str,
) -> dict[str, Any]:
    """按 RAGAS 0.4 指标契约构造最小参数集合，避免向指标传入无效字段。"""
    contracts = {
        "context_precision": {
            "user_input": user_input,
            "reference": reference,
            "retrieved_contexts": retrieved_contexts,
        },
        "context_recall": {
            "user_input": user_input,
            "reference": reference,
            "retrieved_contexts": retrieved_contexts,
        },
        "faithfulness": {
            "user_input": user_input,
            "response": response,
            "retrieved_contexts": retrieved_contexts,
        },
        "answer_relevancy": {"user_input": user_input, "response": response},
        "factual_correctness": {"response": response, "reference": reference},
    }
    try:
        return contracts[metric_name]
    except KeyError as exc:
        raise ValueError(f"不支持的 RAGAS 指标：{metric_name}") from exc


def aggregate(results: list[dict[str, Any]], samples: list[dict[str, Any]]) -> dict[str, Any]:
    """按指标和业务类别计算平均分，不把跳过样本算入分母。"""
    sample_by_id = {sample["id"]: sample for sample in samples}
    metric_values: dict[str, list[float]] = defaultdict(list)
    category_values: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for result in results:
        if result.get("status") == "SKIPPED":
            continue
        category = sample_by_id[result["id"]]["category"]
        for name, value in result.get("scores", {}).items():
            if isinstance(value, (int, float)):
                metric_values[name].append(float(value))
                category_values[category][name].append(float(value))
    return {
        "overall": {
            name: sum(values) / len(values) if values else None
            for name, values in metric_values.items()
        },
        "by_category": {
            category: {
                name: sum(values) / len(values) if values else None
                for name, values in metrics.items()
            }
            for category, metrics in category_values.items()
        },
    }


async def run(args: argparse.Namespace) -> None:
    """执行评估、限制并发并保存 JSON 报告。"""
    settings = Settings()
    samples = load_samples(args.dataset)
    if args.limit is not None:
        if args.limit < 1:
            raise ValueError("--limit 必须是正整数")
        samples = samples[: args.limit]
    runtime_results = load_runtime_results(args.runtime_results)
    metrics = build_metrics(settings)
    semaphore = asyncio.Semaphore(settings.ragas_evaluator_max_concurrency)

    async def limited(sample: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            runtime = runtime_results.get(f"{sample['id']}::{args.mode}") or runtime_results.get(sample["id"])
            return await score_sample(sample, runtime, metrics)

    results = await asyncio.gather(*(limited(sample) for sample in samples))
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "evaluator": {
            "provider": "aliyun_bailian",
            "base_url": str(settings.embedding_base_url),
            "model": settings.ragas_evaluator_model_name,
            "embedding_model": settings.embedding_model_name,
        },
        "dataset": str(args.dataset),
        "runtime_results": str(args.runtime_results) if args.runtime_results else None,
        "count": len(samples),
        "summary": aggregate(results, samples),
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"RAGAS 评估完成：{len(samples)} 条；报告：{args.output}")


def main() -> None:
    """解析参数并启动异步评估。"""
    parser = argparse.ArgumentParser(description="运行百炼 DeepSeek-V4-Flash-0731 RAGAS 基线评估")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "evals" / "datasets" / "p0_baseline.jsonl",
    )
    parser.add_argument(
        "--runtime-results",
        type=Path,
        help="可选的真实运行结果 JSONL，每行包含 id、retrieved_contexts 和 response",
    )
    parser.add_argument("--mode", choices=["baseline", "current"], default="current", help="从 baseline/current 配对运行结果中选择一组")
    parser.add_argument(
        "--limit",
        type=int,
        help="仅评估前 N 条样本，用于低成本连通性和小批量验证",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "evals" / "reports" / "ragas-baseline.json",
    )
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
