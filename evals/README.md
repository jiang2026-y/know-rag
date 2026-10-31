# P0 RAG 评测集

`datasets/p0_baseline.jsonl` 是当前项目的第一版脱敏基线集，共 20 条人工编写用例。

数据来源仅包括：

- `scripts/seed_rag_evaluation_data.py` 创建的三篇 `P0测试-` 知识文档；
- `EVAL-CAR-001` 测试车辆；
- `infra/neo4j/seed/public_vehicle_graph.cypher` 中的公开测试图谱；
- 已确认的顾客、员工、领导和非汽车问题权限规则。

该数据集不包含真实顾客、员工、手机号、车牌、车架号或线上会话内容，不能代表生产业务覆盖度。

`contexts` 是人工整理的参考上下文，不是每次评测的实际召回结果。运行 RAGAS 时，问题仍需重新执行当前问答链路，并将实际召回证据传给评审指标；否则无法发现检索、RRF 或重排回归。

每条记录还包含 `expected_strategies`、`expected_source_types` 和 `expected_intent`，这些字段使用确定性断言校验，不交给 RAGAS 判断权限和路由。

## 运行 RAGAS

评测复用项目已有的百炼连接，不需要新增 RAGAS 专用 API 配置。确认 `.env` 已填写现有的百炼 API Key，并使用以下模型：

```env
EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
EMBEDDING_API_KEY=现有的百炼_API_Key
RAGAS_EVALUATOR_MODEL_NAME=deepseek-v4.1-flash
RAGAS_EVALUATOR_MAX_TOKENS=16384
```

评测中的 `AnswerRelevancy` 还会调用项目已配置的 `text-embedding-v4`，因此 `.env` 中也必须填写现有的 `EMBEDDING_API_KEY`；评测脚本不会把该密钥写入报告。

安装可选评测依赖（RAGAS 0.4.3，与当前 LangChain 1.x 兼容）：

```powershell
python -m pip install -e ".[eval]"
```

### 1. 采集真实链路结果（推荐）

`run_rag_offline_eval.py` 的 mock 模式会把数据集里的参考上下文与标准答案原样写入运行结果，
只能验证评分流程，**不能代表线上召回与回答质量**。真实评测必须先用当前应用链路采集：

```powershell
# 只做前置检查（MySQL/ES/Neo4j、重排配置、API Key），不调用模型
python scripts/collect_live_rag_results.py --dry-run

# 先跑 5 条 smoke，确认证据与回答非空、rerank_status=APPLIED
python scripts/collect_live_rag_results.py --limit 5 --output evals/reports/live/smoke-runtime-results.jsonl

# 跑满 20 条；中途失败可加 --resume 续跑，--cleanup 清理本次生成的 eval- 会话
python scripts/collect_live_rag_results.py
```

采集前置条件：

- Elasticsearch 8.x（含 `analysis-ik`）与 Neo4j 已启动，公开车型图谱已加载。
- 3 篇 `P0测试-` 文档为 `VECTOR_STORED`，`EVAL-CAR-001` 绑定到评测顾客（默认 id `1`）。
- `.env` 已配置 `CHAT_RERANK_BASE_URL`；未配置时 dry-run 会直接失败，不会静默跳过重排。
- CUSTOMER 样本使用 `--customer-id`（默认 `1`），STAFF 样本使用合成工号 `EVAL-STAFF`，不需要真实员工账号。

每条样本会创建独立会话（`eval-` 前缀，不触发标题生成），记录重排后的证据正文、最终回答、
意图/路由/重排状态与耗时；单条失败标记为 `ERROR` 并继续执行其余样本。

### 2. 评分与渲染报告

```powershell
python scripts/run_ragas_baseline.py `
  --runtime-results evals/reports/live/runtime-results.jsonl `
  --mode current `
  --output evals/reports/live/ragas.json

python scripts/render_ragas_report.py

# 把真实评分并入总报告；样本数少于 20 时会标记为 SMOKE，避免误读为完整评测
python scripts/run_full_rag_evaluation.py `
  --output-dir evals/reports/full-rag `
  --ragas-report evals/reports/live/ragas.json
```

产物：

| 文件 | 说明 |
| --- | --- |
| `evals/reports/live/runtime-results.jsonl` | 真实链路逐条结果 |
| `evals/reports/live/ragas.json` | RAGAS 五项指标原始评分 |
| `evals/reports/live/ragas-report.md` | 中文 Markdown 报告（总体/分类/逐条/异常） |
| `evals/reports/full-rag/full-rag-evaluation.{json,md}` | 汇总报告（含 MinerU、Prompt 与 RAGAS） |

### 3. 静态基线评估（仅验证流程）

运行静态基线评估：

```powershell
python scripts/run_ragas_baseline.py
```

首次验证或需要控制调用量时，可先只评估前 3 条：

```powershell
python scripts/run_ragas_baseline.py --limit 3 --output evals/reports/ragas-smoke.json
```

如果已有真实运行结果，可使用每行包含 `id`、`retrieved_contexts` 和 `response` 的 JSONL 覆盖基线字段：

```powershell
python scripts/run_ragas_baseline.py --runtime-results evals/reports/live/runtime-results.jsonl --mode current
```

报告默认写入 `evals/reports/ragas-baseline.json`。评测模型只用于离线评分，不参与线上问答；不要把真实顾客信息写入评测集或运行结果。

成本参考：20 条样本约产生 20 次回答、60 次小模型调用、20 次重排、约 100 次评审调用与向量化调用；
先用 `--limit 5` 验证链路，再跑全量。
## know-engine 评测迁移

`datasets/intent_comparison.jsonl` 和 `evals/prompts/intent-recognition-old-prompt.txt` 是把早期版本意图提示词的评测思路脱敏转换而来；当前实现使用 Python，不引入其他语言的构建链，也不写入任何密钥。

## 同源 MinerU 文档 RAGAS 评测

`datasets/mineru_ragas.jsonl` 与压缩包中的 `MinerU_markdown_r7-product-manual-for-testset.processed.md` 同源；
先将 30 条检索样本转换为 RAGAS 契约：

```powershell
python scripts/convert_mineru_eval_dataset.py
```

真实链路评测要求 `CHAT_RERANK_BASE_URL`、`CHAT_RERANK_API_KEY`（或可用的重排连接）已配置，且隔离评测服务已经启动。
导入同源文档时使用独立标题，脚本可重复执行而不会创建同名文档：

```powershell
python scripts/seed_mineru_evaluation_document.py --apply
```

先做不调用模型的前置检查，再采集 5 条 smoke：

```powershell
python scripts/collect_live_rag_results.py `
  --dataset evals/datasets/mineru_ragas.jsonl `
  --dataset-kind mineru --limit 5 --dry-run
python scripts/collect_live_rag_results.py `
  --dataset evals/datasets/mineru_ragas.jsonl `
  --dataset-kind mineru --limit 5 `
  --output evals/reports/live/mineru-runtime-results.jsonl
```

确认 smoke 的运行结果后再评分：`status=OK`、证据非空且 `metadata.rerank_status=APPLIED` 的样本可以直接进入评分；如果出现 `SKIPPED` 或空证据，必须作为异常样本保留，不能伪装为评测通过。随后运行完整 30 条并评分：

```powershell
python scripts/collect_live_rag_results.py `
  --dataset evals/datasets/mineru_ragas.jsonl `
  --dataset-kind mineru `
  --output evals/reports/live/mineru-runtime-results.jsonl
python scripts/run_ragas_baseline.py `
  --dataset evals/datasets/mineru_ragas.jsonl `
  --runtime-results evals/reports/live/mineru-runtime-results.jsonl `
  --mode current --output evals/reports/live/mineru-ragas.json
python scripts/render_ragas_report.py `
  --input evals/reports/live/mineru-ragas.json `
  --output evals/reports/live/mineru-ragas-report.md `
  --title "MinerU 同源文档 RAGAS 评测报告"
```

评测脚本只写入 `evals/` 报告和带 `eval-` 前缀的临时会话；报告不包含 API Key。若中途失败，可对采集命令追加 `--resume` 续跑。

### 当前有效的 MinerU 评测产物

当前使用 `deepseek-v4.1-flash` 和 `text-embedding-v4`，真实运行结果来自同一份 30 条采集文件：

| 文件 | 样本 | 状态 | 说明 |
| --- | ---: | --- | --- |
| `evals/reports/live/mineru-ragas-summary.json` | 30 | `28 OK / 2 PARTIAL` | 三份有效分段结果的总体汇总 |
| `evals/reports/live/mineru-ragas-summary-report.md` | 30 | 汇总报告 | 中文总体/逐条/异常报告 |
| `evals/reports/live/mineru-ragas-smoke-v41.json` | 1 | `OK` | 单条 smoke，五项指标均有分数 |
| `evals/reports/live/mineru-ragas-10.json` | 10 | `8 OK / 2 PARTIAL` | `mineru-002` 空证据，`mineru-008` 忠实度输出过长 |
| `evals/reports/live/mineru-ragas-11-20.json` | 10 | `10 OK` | 无指标异常 |
| `evals/reports/live/mineru-ragas-21-30.json` | 10 | `10 OK` | 无指标异常 |

`mineru-002` 的意图识别将汽车问题判为通用问答，导致无召回；`mineru-008` 的 `faithfulness` 评审输出超过模型输出上限。两类异常都必须在报告中单独说明，不能将缺失分数当作 0 分或补造分数。旧模型、额度错误和未完成运行产生的报告不作为有效结果保留。

运行意图 Prompt 对比（默认仅 mock）：

```powershell
python scripts/run_prompt_comparison.py --dataset evals/datasets/intent_comparison.jsonl --mode mock --output evals/reports/intent-comparison.json
```

运行 RAG baseline/current 离线适配器：

```powershell
python scripts/run_rag_offline_eval.py --dataset evals/datasets/p0_baseline.jsonl --mode mock --output evals/reports/runtime-results.jsonl
```

禁止将 API Key、真实客户/车辆数据写入数据集或报告。mock 结果仅用于校验数据流和报告格式；真实模型评测必须显式配置并单独执行。
