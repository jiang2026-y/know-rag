# RAG 对照评测报告

本报告由离线适配器和 RAGAS 脚本生成。`mock` 模式使用脱敏数据集中的参考上下文，不代表线上真实召回效果；只有接入真实运行结果后，才可解释为当前 RAG 链路指标。

## 当前状态

- 评测分为 baseline/current 配对样本，字段和样本 ID 必须一致。
- `ERROR`、`SKIPPED` 或缺少召回结果的样本不计入平均分。
- 未完成真实链路和同配置 RAGAS 评审前，不报告“提升百分比”。

## 运行

```powershell
python scripts/run_rag_offline_eval.py --dataset evals/datasets/p0_baseline.jsonl --mode mock --output evals/reports/runtime-results.jsonl
python scripts/run_ragas_baseline.py --runtime-results evals/reports/runtime-results.jsonl --output evals/reports/ragas-current.json
```

## 限制

当前 P0 集只有 20 条人工脱敏样本，mock 适配器不访问 Elasticsearch、MySQL、Neo4j 或模型 API，不能替代生产验收。
