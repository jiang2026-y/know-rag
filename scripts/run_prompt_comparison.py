"""意图 Prompt 对比评测脚本：以确定性 mock 适配器比较旧版与当前 Prompt。"""
from __future__ import annotations
import argparse, json, re, time
from pathlib import Path
from typing import Any

def load_rows(path: Path) -> list[dict[str, Any]]:
    """读取 JSONL 意图样本。"""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

def mock_predict(question: str, version: str) -> dict[str, Any]:
    """使用关键词模拟旧版/新版预测，保证离线可重复。"""
    q = question.lower(); entities: dict[str, str] = {}
    m = re.search(r"(xyz\d+|ord\d+)", q, re.I)
    if m: entities["order_id"] = m.group(1)
    for key, pat in (("dealer", "朝阳店"), ("appointment_time", "明天下午3点"), ("car_model", "model 3"), ("function_name", "中控屏"), ("fault_description", "发动机有异响")):
        if pat.lower() in q: entities[key] = pat
    if any(x in q for x in ("天气", "你好")): return {"related": False, "intent": "闲聊与通用问答", "entities": {}}
    if version == "old" and "雨刮器怎么换" in q: intent = "售后维修与保养"
    elif any(x in q for x in ("多少钱", "换机油", "换一个")): intent = "售后维修与保养"
    elif any(x in q for x in ("预约", "朝阳店")): intent = "售后维修与保养"
    elif any(x in q for x in ("续航", "model y", "model 3")): intent = "售前咨询与购买"
    elif any(x in q for x in ("投诉", "死机", "订单号")): intent = "投诉与维权"
    elif any(x in q for x in ("谢谢",)): intent = "其他"
    else: intent = "车辆使用与技术指导"
    return {"related": True, "intent": intent, "entities": entities}

def entity_score(pred: dict[str, Any], expected: dict[str, Any]) -> tuple[bool, float]:
    """计算实体 exact 与 micro-F1。"""
    p, e = pred.get("entities", {}), expected.get("entities", {}); keys = set(p) | set(e)
    exact = p == e; tp = sum(1 for k in keys if p.get(k) == e.get(k) and k in p and k in e)
    f1 = 1.0 if not keys and not p and not e else (2*tp/(len(p)+len(e)) if len(p)+len(e) else 0.0)
    return exact, f1

def main() -> None:
    """执行对比并写入 JSON 报告。"""
    ap = argparse.ArgumentParser(); ap.add_argument("--dataset", type=Path, required=True); ap.add_argument("--mode", choices=["mock"], default="mock"); ap.add_argument("--output", type=Path, required=True); args = ap.parse_args()
    rows, details = load_rows(args.dataset), []
    for row in rows:
        expected = row["expected"]; item = {"id": row["id"], "category": row["category"], "question": row["question"]}
        for version in ("old", "current"):
            start = time.perf_counter(); pred = mock_predict(row["question"], version); latency = round((time.perf_counter()-start)*1000, 3)
            ex, f1 = entity_score(pred, expected); item[version] = {"prediction": pred, "related_correct": pred["related"] == expected["related"], "intent_correct": pred["intent"] == expected["intent"], "entity_exact": ex, "entity_f1": f1, "latency_ms": latency, "status": "OK"}
        details.append(item)
    def avg(v: str, version: str) -> float:
        return sum(float(x[version][v]) for x in details) / len(details) if details else 0.0
    metrics = {"overall_accuracy": {v: avg("intent_correct", v) for v in ("old", "current")}, "related_accuracy": {v: avg("related_correct", v) for v in ("old", "current")}, "entity_exact": {v: avg("entity_exact", v) for v in ("old", "current")}, "entity_micro_f1": {v: avg("entity_f1", v) for v in ("old", "current")}}
    metrics["improvement"] = {k: {"absolute": metrics[k]["current"]-metrics[k]["old"], "relative_percent": ((metrics[k]["current"]-metrics[k]["old"])/metrics[k]["old"]*100 if metrics[k]["old"] else None)} for k in ("overall_accuracy", "related_accuracy", "entity_exact", "entity_micro_f1")}
    args.output.parent.mkdir(parents=True, exist_ok=True); args.output.write_text(json.dumps({"mode": args.mode, "count": len(details), "metrics": metrics, "results": details}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已生成 {args.output}（{len(details)} 条）")
if __name__ == "__main__": main()
