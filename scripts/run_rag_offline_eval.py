"""离线 RAG 评测适配器：生成 baseline/current 两组可重复 JSONL 结果。"""
from __future__ import annotations
import argparse, json, time
from pathlib import Path
from typing import Any

def load(path: Path) -> list[dict[str, Any]]:
    """读取 P0 JSONL 数据集。"""
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]

def main() -> None:
    """执行 mock 检索链路并写出统一结果。"""
    ap = argparse.ArgumentParser(); ap.add_argument("--dataset", type=Path, required=True); ap.add_argument("--mode", choices=["mock"], default="mock"); ap.add_argument("--output", type=Path, required=True); args = ap.parse_args()
    out: list[dict[str, Any]] = []
    for row in load(args.dataset):
        start = time.perf_counter(); related = bool(row.get("expected_related", True)); contexts = list(row.get("contexts", [])); elapsed = round((time.perf_counter()-start)*1000, 3)
        for mode in ("baseline", "current"):
            out.append({"id": row["id"], "mode": mode, "retrieved_contexts": contexts if related else [], "response": str(row.get("answer", "")), "sources": row.get("expected_source_types", []), "latency_ms": elapsed, "status": "SKIPPED" if not related else "OK", "error": None, "metadata": {"adapter": "mock", "stage_order": ["intent", "rewrite", "route", "retrieve", "rerank", "answer"]}})
    args.output.parent.mkdir(parents=True, exist_ok=True); args.output.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in out) + "\n", encoding="utf-8"); print(f"已生成 {args.output}（{len(out)} 条，baseline/current 配对）")
if __name__ == "__main__": main()
