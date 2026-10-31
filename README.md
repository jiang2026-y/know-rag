# LLMcopy

面向**普通顾客、已购车车主与企业员工**的汽车领域知识管理与受控 RAG 问答系统：
FastAPI 后端 + Next.js 前端，覆盖「文档入库 → 人工审核 → 向量化 → 多源检索 → 带引用流式回答 → 反馈」完整闭环。

![CI](https://github.com/jiang2026-y/know-rag/actions/workflows/ci.yml/badge.svg?branch=know-rag)
![Python](https://img.shields.io/badge/Python-3.10%20%7C%203.13-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688?logo=fastapi&logoColor=white)
![Next.js](https://img.shields.io/badge/Next.js-15-000000?logo=nextdotjs&logoColor=white)
![Elasticsearch](https://img.shields.io/badge/Elasticsearch-8.x-005571?logo=elasticsearch&logoColor=white)
![MySQL](https://img.shields.io/badge/MySQL-8-4479A1?logo=mysql&logoColor=white)

> 当前状态：MVP 迭代中，可演示「文档入库 → 审核 → 向量化 → 多源检索 → 带引用流式回答 → 反馈」全链路，
> 但**尚未完成生产上线验收**。已完成能力、已知缺口与发布检查清单分别见
> [docs/README.md](./docs/README.md) 与 [docs/发布与状态.md](./docs/发布与状态.md)。

## 项目介绍

### 要解决的问题

汽车业务的问答需求天然分成两类：一类是**公开知识**（车型配置、用车技巧、保养政策、营销活动），
另一类是**只对特定人可见的数据**（车主自己的车辆与保险、员工内部的维修工单与技术支持文档）。
把这两类问题交给同一个大模型时，最危险的失误不是「答不出来」，而是「答错了对象」——
把别人的车辆记录、未公开的政策或内部文档讲给了没有权限的人。

### 解决思路

把所有数据源都放在服务端可信边界之后，让大模型只看到**已授权、已限量、可追溯**的证据：
身份与数据范围由服务端 Session 推导；文档先经 `leader` 审核并确定可见范围，通过后才会向量化进入检索；
召回结果还要回 MySQL 复核一次；模型只能拿到复核通过的分片，回答必须带引用，引用不到证据时明确说明而不是编造。
关系库与图库不接受模型生成的 SQL/Cypher，只执行按身份参数化和预登记的查询。

### 面向谁

| 角色 | 典型问题 | 权限边界 |
| --- | --- | --- |
| 普通顾客 | 车型配置、购车政策、试驾与门店信息 | 只能访问标记为公开的知识 |
| 已购车车主 | 我的车辆信息、保险到期、保养与故障处理 | 公开知识 + 绑定在本人 `user_id` 下的车辆记录 |
| 企业员工 | 内部技术文档、售后政策、维修案例 | 公开 + 员工范围知识；领导可额外访问领导范围并可审核发布 |

### 当前形态

这是一个**可本地完整运行、尚未做生产验收**的工程实现：后端 153 项单元测试全部使用替身、不依赖中间件即可运行；
前端提供登录、问答、SSE 流式展示、引用、文档上传审核与赞踩反馈的最小闭环；
离线评测用脱敏数据集跑 RAGAS 五项指标，报告存放在 `evals/reports/`。

## 核心设计

- **权限先于检索**：身份、权限与数据范围一律由服务端 Session 推导，浏览器、模型与 Agent 都不直连数据库或检索引擎。
- **双保险授权**：Elasticsearch 负责按可见标签过滤召回，命中后仍需回 MySQL 复核文档状态与标签，未授权命中直接丢弃。
- **检索只做受控查询**：关系库只允许按当前车主身份的参数化白名单查询，图库只允许固定 Cypher 模板，不接受模型生成的 SQL/Cypher。
- **失败可降级、可审计**：结构化源无证据时回落知识库并记录审计原因；重排、模型与历史读取失败都有明确降级路径。

## 功能一览

| 模块 | 能力 | 状态 |
| --- | --- | --- |
| 身份与权限 | 顾客/员工登录、服务端 Session、HttpOnly Cookie、Argon2id 密码、员工权限审计与 HR 初始密码 | ✅ 已实现 |
| 知识入库 | PDF/DOC/DOCX 解析转 Markdown、 2种切片策略、领导审核发布、文档生命周期 | ✅ 已实现 |
| 混合检索 | ES 向量 + 中文全文召回、RRF 融合、MySQL 二次授权、父子块补全、父块三级缓存 | ✅ 已实现 |
| 智能问答 | 意图识别、查询改写、多源路由与回落、rerank、SSE 六类事件、多轮记忆、引用溯源 | ✅ 已实现 |
| 会话与反馈 | 赞踩反馈、异步标题生成、会话列表与详情 | ✅ 已实现 |
| 任务可靠性 | Celery + Relier 任务状态机、自动重试、DLQ 与 leader 重放、Redis 分布式锁 | ✅ 已实现 |
| 离线评测 | 脱敏数据集、RAGAS 五项指标、意图提示词对比 | ✅ 已实现 |
| 文档治理 | 文档版本上传与切换、归档与删除、独立表格切分模式 | 🚧 待补齐 |
| 结构化数据 | 当前车主 `my_car` 受控查询 | 🚧 受控 Text2SQL、动态表、车型/订单卡片待做 |
| Agent 运行时 | 独立 Runtime 骨架与内部工具契约 | 🚧 骨架已建、编排未迁移 |
| 生产化 | 统一错误模型、结构化脱敏日志、指标与告警、预生产混沌验收 | 📋 规划中 |

## 主要能力

### 知识入库

- 上传 `PDF`、`DOC`、`DOCX`（MinerU 解析），源文件与产物存入私有 MinIO。
- 切片策略：`TITLE`（默认，按标题层级父子切分）、`SMART`（重叠 10%）、`LENGTH`、`REGEX`、`SEPARATOR`；
  参数随文档版本持久化，非法参数直接返回 422 且不落库。
- 超长章节保留完整父块（`skip_embedding=1`）并生成带重叠的可检索子块（元数据含 `parentChunkId`）。
- 员工上传后由 `leader` 审核并确定可见范围（`VISITOR`/`STAFF`/`LEADER`），审核通过才向量化：
  DashScope `text-embedding-v4`、1536 维，写入本地 Elasticsearch（KNN + 中文全文 + metadata）。
- 文档生命周期独立于处理状态，支持 `ENABLED`/`DISABLED`/`ARCHIVED`/`DELETED`。

### 智能问答

```text
用户提问
  → 服务端 Session 构造身份与权限上下文
  → 读取该会话最近 N 条历史消息（默认 10 条，按字符预算裁剪）
  → qwen3.5-flash：意图识别 → 查询改写 → 数据源路由（最多两路）
  → 并行召回：ES 混合检索 / 当前车主受控 MySQL / 公开车型图谱
  → 结构化源无证据时回落知识库（记录 fallbacks 审计）
  → 跨源 RRF 融合
  → MySQL 二次授权 + 父块三级缓存补全（请求内 → Redis → MySQL）
  → qwen3-rerank 重排（未配置或失败时安全降级为 RRF 结果）
  → qwen3.7-plus 生成 SSE 回答与引用
```

- SSE 六类事件：`progress`、`query_rewrite`、`answer_delta`、`citation`、`error`、`done`；同一主体同一时刻只允许一条回答流，
  15 秒心跳、最长 120 秒，断开连接即取消上游模型调用，失败或空回答不保存部分消息。
- 会话：服务端 Session + HttpOnly Cookie，Redis 存储登录态；会话标题由 `qwen3.5-flash` 异步生成，不阻塞回答。
- 反馈：完整回答可提交 `LIKE`/`DISLIKE`，幂等且保留最近一次评价、引用、模型与权限快照。

### 治理与运维

- 文档处理使用 `queued` → `processing` → `completed`/`failed`/`cancelled` 持久化状态机，Celery + Relier 驱动，
  自动重试上限 3 次，耗尽后进入 DLQ，仅 `leader` 可查询和人工重放。
- Redis 分布式锁（随机令牌 + Lua 释放 + 自动续租）保护上传、切片与向量化。
- 员工权限变更审计、HR 内部初始密码接口、Python 代码分析沙箱（Docker、无网络、资源受限）。
- 离线评测：脱敏数据集 + RAGAS 五项指标，报告落在 `evals/reports/`。

## 技术栈

| 分类 | 选型 |
| --- | --- |
| 后端 | Python 3.10-3.13、FastAPI、Uvicorn、Pydantic v2 |
| 前端 | Next.js App Router、React、TypeScript |
| 关系库 | MySQL 8（PyMySQL + Alembic 迁移） |
| 检索 | Elasticsearch 8.x（KNN + IK 中文分词 + metadata 过滤） |
| 图库 | Neo4j 5（P0 仅公开车型图谱，只读账号） |
| 对象存储 | MinIO（私有对象 + 预签名下载） |
| 缓存与任务 | Redis 7+（Session、分布式锁、父块正文缓存）、Celery 5.4 + Relier |
| 模型 | DashScope：`qwen3.5-flash`（意图/改写/路由/标题）、`qwen3.7-plus`（回答）、`qwen3-rerank`（重排）、`text-embedding-v4`（1536 维） |
| 文档解析 | MinerU Precision Extract API（PDF/Word） |

## 目录结构

```text
LLMcopy/
├─ src/llmcopy/                    FastAPI 后端（分层：modules/*/{api,application,domain,infrastructure}）
│  ├─ modules/identity/            登录、服务端 Session、员工权限与 HR 开户
│  ├─ modules/knowledge/           文档上传、任务、审核、切片、向量化、检索与缓存
│  ├─ modules/conversation/        会话、消息、流式回答、反馈
│  ├─ modules/qa/                  意图识别、查询改写、路由、多源召回、重排
│  ├─ modules/vehicle/             当前车主车辆范围与受控车辆查询
│  └─ tasks/                       Celery/Relier 任务入口
├─ apps/web/                       Next.js 最小前端（登录、问答、上传审核、反馈）
├─ apps/agent_runtime/             Agent Runtime 骨架（尚未接管编排）
├─ alembic/                        数据库迁移
├─ docs/                           产品需求、技术选型、可靠任务、发布与状态
├─ evals/                          脱敏评测数据集、脚本与历史报告
├─ infra/                          Redis / Neo4j / 评测环境 / Agent 沙箱的 Compose 与说明
├─ packages/contracts/             SSE 事件 JSON Schema
├─ scripts/                        运维与评测脚本
└─ tests/                          单元测试（全部使用替身，不依赖真实中间件）
```

## 快速开始

### 1. 前置依赖

| 组件 | 用途 | 是否必需 |
| --- | --- | --- |
| MySQL 8 | 业务事实源 | 必需 |
| Redis 7+ | 登录 Session、分布式锁、父块缓存 | 必需（`infra/redis/compose.yaml`） |
| Elasticsearch 8.x + `analysis-ik` | 向量与中文全文检索 | 必需 |
| MinIO | 原文件、Markdown 与图片 | 必需 |
| DashScope API Key | 意图/改写/路由/回答/向量化/重排 | 必需 |
| MinerU Token | PDF/Word 解析 | 上传 PDF/Word 时必需 |
| Neo4j 5 | 公开车型图谱（`infra/neo4j/compose.yaml`） | 可选 |
| Node.js 20 LTS+ | Next.js 前端 | 需要 Web 页面时 |

### 2. 安装后端

```powershell
python -m pip install -e ".[dev]"
Copy-Item .env.example .env      # 填写 MySQL、Redis、MinIO、Elasticsearch、DashScope、MinerU 配置
alembic upgrade head
uvicorn rail_rag.main:app --reload --port 8001
```

### 3. 启动异步任务（Worker 与周期任务）

```powershell
.\.venv\Scripts\celery.exe -A rail_rag.tasks.celery_app:celery_app worker --loglevel=INFO --concurrency=1
.\.venv\Scripts\celery.exe -A rail_tag.tasks.celery_app:celery_app beat --loglevel=INFO
```

### 4. 启动前端

```powershell
cd apps/web
npm install
npm run dev
```

浏览器访问 `http://localhost:3000`，前端通过同源 `/backend/*` 代理访问 FastAPI，不持有任何密钥。

## 主要接口

| 接口 | 方法 | 说明 |
| --- | --- | --- |
| `/api/auth/customer/sessions`、`/api/auth/staff/sessions` | POST | 顾客 / 员工登录，下发 HttpOnly Session Cookie |
| `/api/chat/conversations` | POST | 创建会话并保存首条消息，后台异步生成标题 |
| `/api/chat/conversations/{id}/messages:stream` | POST | SSE 流式回答（六类事件 + 引用） |
| `/api/messages/{message_id}/feedback` | POST | 回答赞踩反馈（需 `Idempotency-Key`） |
| `/api/documents/upload` | POST | 上传文档并选择切片策略（可选参数） |
| `/api/documents/tasks/{task_id}`、`/retry`、`/cancel` | GET/POST | 任务状态、重试与取消 |
| `/api/documents/review-queue`、`/{id}/review` | GET/POST | 审核队列与领导审核发布 |
| `/api/documents/knowledge/search` | POST | 受控知识检索（用于联调与评测） |
| `/api/admin/tasks/dead-letter`、`/{id}/replay` | GET/POST | 死信查询与 leader 重放 |

所有业务接口同时挂在 `/api/*` 与 `/api/v1/*` 两套前缀下（`src/llmcopy/main.py` 中统一注册），
内部接口 `/internal/v1/*` 不对外暴露。

## 测试与评测

RAGAS 真实链路评测使用的 30 条 MinerU 样本。mock 模式只用于检查数据流和报告格式，不能代表真实召回质量。真实评测需要先启动隔离 Docker 服务、导入 MinerU 文档，再采集当前问答链路结果。


当前有效的分段结果：

- `mineru-ragas-summary.json`：30 条总体汇总，28 条 `OK`、2 条 `PARTIAL`；
- mineru-ragas-10.json：前 10 条，8 条 OK、2 条 PARTIAL；
- mineru-ragas-11-20.json：10 条全部 OK；
- mineru-ragas-21-30.json：10 条全部 OK。

30 条总体均值（按各指标有效分数计算）：context_precision 0.9427、faithfulness 0.9517、answer_relevancy 0.8934、factual_correctness 0.5947。总体汇总报告见 `evals/reports/live/mineru-ragas-summary-report.md`。

前 10 条中的 mineru-002 存在意图误判导致的空召回，mineru-008 的 faithfulness 评审输出过长；这些异常必须单独记录，不能将缺失分数当作 0 分。旧模型、额度错误和未完成运行产物不作为有效结果。详细前置条件、异常说明和报告路径见 evals/README.md。

## 文档导航

| 文档 | 内容 |
| --- | --- |
| [docs/README.md](./docs/README.md) | 全部文档入口、当前实现概览与已知缺口 |
| [docs/MVP产品需求文档.md](./docs/MVP产品需求文档.md) | 产品定位、P0 范围、验收标准与里程碑 |
| [docs/需求规格说明书.md](./docs/需求规格说明书.md) | 功能需求、API/SSE 约束、非功能需求 |
| [docs/技术栈选型.md](./docs/技术栈选型.md) | 中间件、模型、缓存与任务选型结论 |
| [docs/可靠任务运行手册.md](./docs/可靠任务运行手册.md) | 任务 Redis、Worker、DLQ 与备份运维 |
| [docs/发布与状态.md](./docs/发布与状态.md) | 发布前检查清单与最近一次验证结果 |
| [apps/web/README.md](./apps/web/README.md) | 前端运行方式与页面边界 |
| [evals/README.md](./evals/README.md) | 评测数据集、脚本与报告说明 |

## 当前状态与路线图

已完成：文档入库与审核发布、混合检索与二次授权、多源路由与回落、父子块补全与三级缓存、重排与流式回答、
多轮记忆、引用与反馈、任务状态机与 DLQ、RAGAS 离线评测、最小 Web 页面。

尚未完成：真实环境端到端联调与生产验收、Agent Runtime 独立落地、`/api/v1` 兼容迁移、
会话归档与删除、文档版本切换与归档删除、受控 Text2SQL 与车型/订单卡片交互、统一错误模型与可观测性闭环。

## 许可

本仓库暂未附开源许可协议。

## 贡献

提交前请阅读 [AGENTS.md](./AGENTS.md) 中的开发约定：所有文档使用中文，新增文件需在文件头说明职责，
新增或修改内容必须基于现有目录与配置做增量改动，并在涉及架构、Schema、模型、成本或安全的决策前先确认。
