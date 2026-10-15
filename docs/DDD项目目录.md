# LLMcopy P0：DDD 项目目录与边界设计

> 文档状态：目标目录已确认，现有代码渐进迁移｜更新时间：2026-08-04
>
> 配套文档：[技术栈选型](./技术栈选型.md) · [需求规格说明书](./需求规格说明书.md)

## 1. P0 的边界上下文

本项目采用“Next.js Web 前端 + FastAPI 业务后端 + 独立 Agent 运行时”的形态。FastAPI `api_service` 持有业务聚合、身份和权限；Agent 只负责编排和受控工具调用；Next.js 只负责 Web 展示与浏览器交互。

| 限界上下文 | 类型/优先级 | 负责什么 | P0 状态 |
| --- | --- | --- | --- |
| Identity & Access（身份与访问控制） | 核心 | 顾客和员工的 Redis 服务端 Session、MySQL 员工账号认证、基于 `status` 与 `job` 的员工分类权限、会话撤销与审计。 | 必须 |
| Knowledge Management（知识管理） | 核心 | 员工上传、领导审核、MinerU 解析、版本、生命周期、父子切片、Embedding 和 Elasticsearch 投影。 | 必须 |
| Vehicle Service（车辆服务） | 核心 | 车主与订单、车辆、保养、质保和服务记录的受控查询；只向其他上下文提供权限过滤后的 DTO。 | 必须 |
| Conversation（会话） | 核心 | 会话、消息、会话归属、引用关联与历史读取。 | 必须 |
| Q&A Gateway（问答接入） | 支撑 | Web 问答 API、SSE 转发、请求幂等、任务状态查询、回答引用与反馈的业务侧入口。 | 必须 |
| Administration（后台运营） | 支撑 | 员工访问变更、知识审核、可见范围配置、任务重试、反馈处理和运营查询。 | 必须 |
| Agent Orchestration（Agent 编排） | 通用技术上下文 | 意图、改写、工具选择、检索编排、重排、Prompt、模型调用、Relier 任务恢复和回答事件。 | 必须，但不拥有业务聚合 |
| Graph Projection（关系图谱投影） | 支撑 | Neo4j 中的身份、组织、车型、知识域和业务关系投影；只提供受控图查询。 | 必须 |

### 1.1 聚合、读模型与事件边界

- **EmployeeAccess / AccessContext**：员工账号状态、岗位、部门、权限快照和会话撤销属于 Identity & Access。MySQL 是授权事实源；Neo4j 只能是关系查询投影。
- **KnowledgeDocument / DocumentProcessingTask**：文档、版本、生命周期和处理状态属于 Knowledge Management；`KnowledgeSegment`、Elasticsearch 文档和向量为可重建投影，不由 UI 直接写入。
- **VehicleScope**：车主可访问车辆、订单和服务记录的范围由 Vehicle Service 根据可信主数据计算；其他模块只能消费脱敏、授权后的 DTO。
- **Conversation**：会话、消息和引用关联属于 Conversation；每个私有查询以会话归属和最新 `AccessContext` 双重校验。
- **Feedback / QueryRecord**：反馈、检索摘要、回答摘要和运营读模型由 Q&A Gateway / Administration 保存；完整 Prompt、密钥和不必要的个人数据不得进入日志。
- **AgentTask**：Celery/Relier 任务、重试、DLQ 和运行事件属于 Agent Orchestration 的基础设施记录，不可代替业务状态机或越过 FastAPI 修改业务聚合。

### 1.2 上下文映射

```text
Identity & Access ──(AccessContext DTO)──> Vehicle / Knowledge / Conversation / Q&A
Vehicle Service ──(authorized vehicle DTO)──> Q&A Gateway
Knowledge Management ──(retrieval port)──> Agent Orchestration
Graph Projection ──(controlled graph DTO)──> Agent Orchestration
Conversation / Q&A ──(internal request DTO)──> Agent Orchestration
Agent Orchestration ──(ACL + internal HTTP)──> api_service controlled tools
Administration ──(application ports)──> Knowledge / Q&A
```

Agent 不得 import `src/llmcopy/modules/*` 的领域类，也不得直接连接 MySQL、Elasticsearch、MinIO、Neo4j 或业务 Redis。它只可使用受限 Celery/Relier Broker Redis、DashScope 模型凭据及 FastAPI 内部工具凭据；权限、数据范围和审计由 `api_service` 再次执行。

## 2. 当前仓库与渐进迁移原则

当前已实际存在的业务后端位于 `src/llmcopy/`，已完成 `identity`、`knowledge`、`conversation` 的模块化迁移；`vehicle` 已实现只读当前车主车辆范围，`qa` 已实现意图识别；`apps/web/` 已提供不依赖 UI 库的最小 Next.js 登录、会话与 POST SSE 问答页面。仓库根目录的 `alembic/` 是生产 MySQL Schema 的唯一迁移入口，`infra/agent_sandbox/` 已包含独立 Docker Python 3.13 沙箱。

本文件**不要求立即移动或重命名**这些已有文件。新增功能按目标目录进入；原有功能在修改时渐进迁移，并保留 API 契约和测试。禁止为“目录完整”创建无职责的空业务文件，也禁止同时维护两套业务领域模型。

## 3. 目标仓库目录

```text
LLMcopy/
├─ apps/
│  ├─ web/                                      # Next.js App Router + React + TypeScript
│  │  ├─ src/
│  │  │  ├─ app/                                # 页面、布局、路由组和薄 Route Handler
│  │  │  │  ├─ (public)/                        # 公开问答入口
│  │  │  │  ├─ (auth)/                          # 顾客与员工账号密码登录页面
│  │  │  │  ├─ (workspace)/                     # 登录后的会话、文档管理、任务与反馈页面
│  │  │  │  └─ api/                             # 仅同源辅助；不复制 FastAPI 领域逻辑
│  │  │  ├─ modules/                            # 仅前端展示模块，不持有业务聚合
│  │  │  │  ├─ identity/presentation/           # 登录、登出和会话状态 UI
│  │  │  │  ├─ conversation/presentation/       # 会话列表、消息、SSE 消费与引用展示
│  │  │  │  ├─ knowledge/presentation/          # 后台文档、任务状态与受控下载 UI
│  │  │  │  ├─ admin/presentation/              # 审核、反馈和运营页面
│  │  │  │  └─ shared/ui/                       # 无业务归属的基础组件
│  │  │  ├─ lib/
│  │  │  │  ├─ api/                             # FastAPI `/api/v1` 类型化客户端与 SSE 客户端
│  │  │  │  ├─ auth/                            # 浏览器侧非敏感会话辅助
│  │  │  │  └─ config/                          # Web 公共配置；禁止放 Secret
│  │  │  └─ styles/
│  │  ├─ public/
│  │  └─ tests/                                 # 组件、浏览器交互与 E2E 测试
│  │
│  └─ agent_runtime/                            # 独立 Python Agent 运行时
│     ├─ src/llmcopy_agent/
│     │  ├─ core/                               # 设置、依赖注入、内部服务认证、日志与追踪
│     │  ├─ interfaces/
│     │  │  ├─ http/                            # health、内部运行入口、Pydantic HTTP DTO
│     │  │  └─ celery/                          # Celery/Relier 任务入口与消息适配
│     │  ├─ application/
│     │  │  ├─ commands/                        # 执行问答、重试、取消等命令
│     │  │  ├─ dto/                             # 运行、工具、检索和回答 DTO
│     │  │  ├─ ports/                           # LLM、内部工具、队列、观测等抽象
│     │  │  └─ services/                        # 编排用例、预算和任务策略
│     │  ├─ domain/                             # 仅 Agent 自有规则和状态，不放业务聚合
│     │  ├─ workflows/                          # 意图、检索、重排、回答的可替换工作流
│     │  ├─ acl/                                # FastAPI 工具 DTO 与 Agent DTO 的防腐层
│     │  ├─ tools/                              # 预定义、最小权限的业务工具声明
│     │  ├─ prompts/                            # 版本化 Prompt 模板
│     │  └─ infrastructure/
│     │     ├─ llm/                             # DashScope 模型 Adapter
│     │     ├─ queue/                           # Celery、Relier、DLQ 与 Broker Adapter
│     │     ├─ client/                          # FastAPI 内部工具 HTTP 客户端
│     │     └─ observability/                   # OpenTelemetry、结构化日志和指标
│     └─ tests/                                 # 单元、契约、集成与混沌测试
│
├─ src/llmcopy/                                 # 现有 FastAPI api_service，业务可信边界
│  ├─ main.py                                   # 应用入口、路由注册和生命周期
│  ├─ bootstrap.py                              # 依赖组装；Controller 中不得自行构造基础设施
│  ├─ config/                                   # Settings 与环境变量校验
│  ├─ shared/                                   # 通用异常、trace_id、分页、时间；禁止放业务规则
│  ├─ modules/
│  │  ├─ identity/
│  │  │  ├─ api/                                # HTTP、Session 依赖与输入输出 schema
│  │  │  ├─ application/                        # 身份映射、AccessContext 和授权用例
│  │  │  ├─ domain/                             # 主体、角色、权限版本与领域规则
│  │  │  └─ infrastructure/                     # MySQL 账号与授权数据、Redis Session Adapter
│  │  ├─ knowledge/
│  │  │  ├─ api/
│  │  │  ├─ application/                        # 上传、审核、处理、检索与生命周期编排
│  │  │  ├─ domain/                             # 文档、切片、状态机与切片规则
│  │  │  └─ infrastructure/                     # MinerU、MinIO、ES、Embedding、Redis 锁
│  │  ├─ vehicle/
│  │  │  ├─ api/
│  │  │  ├─ application/                        # 车主范围、业务只读查询与脱敏
│  │  │  ├─ domain/
│  │  │  └─ infrastructure/                     # MySQL 主数据、只读视图 Adapter
│  │  ├─ conversation/
│  │  │  ├─ api/
│  │  │  ├─ application/                        # 会话、消息、引用与归属校验
│  │  │  ├─ domain/
│  │  │  └─ infrastructure/
│  │  ├─ qa/
│  │  │  ├─ api/                                # `/api/v1` 问答、SSE、任务和反馈接口
│  │  │  ├─ application/                        # 请求校验、内部运行请求、记录和反馈编排
│  │  │  ├─ domain/                             # 引用、反馈、幂等与安全响应规则
│  │  │  └─ infrastructure/                     # Agent HTTP 客户端、SSE 事件存储 Adapter
│  │  └─ admin/
│  │     ├─ api/
│  │     ├─ application/                        # 员工访问变更、审核、重试、可见范围、运营用例
│  │     └─ infrastructure/
│  └─ infrastructure/                           # 跨上下文通用技术适配器，不放业务权限
│     ├─ persistence/                           # MySQL 连接、事务和 Alembic 支持
│     ├─ graph/                                 # Neo4j 连接与参数化受控查询基础能力
│     ├─ messaging/                             # 任务投递与事件适配基础能力
│     └─ observability/                         # trace、日志、指标基础能力
│
├─ packages/
│  └─ contracts/                                # 跨进程和跨语言的唯一共享契约
│     ├─ openapi/                               # 外部 `/api/v1` 与内部工具 OpenAPI
│     ├─ events/                                # SSE、任务、DLQ 事件 JSON Schema
│     ├─ python/                                # 从契约生成或封装的 Python 类型
│     └─ typescript/                            # 从契约生成或封装的 TypeScript 类型
│
├─ alembic/                                     # MySQL Schema 的唯一迁移入口
│  └─ versions/                                 # 仅增量、可审计迁移
├─ infra/
│  ├─ agent_sandbox/                            # 已存在的 Docker Python 3.13 沙箱
│  ├─ redis/                                    # 本地 Redis 配置；任务 Redis 另按拓扑配置
│  ├─ compose/                                  # 本地/预生产编排文件，创建时按环境分离
│  └─ observability/                            # 追踪、指标与日志采集配置
├─ tests/
│  ├─ modules/                                  # api_service 各限界上下文测试
│  ├─ contracts/                                # OpenAPI、SSE 与内部工具契约测试
│  ├─ integration/                              # MySQL、ES、MinIO、Neo4j、Redis 集成测试
│  ├─ rag/                                      # 公开、车主、员工三类检索与越权评测集
│  └─ security/                                 # 会话、员工账号认证、越权、SSRF 与沙箱测试
└─ docs/
```

目录中 `apps/web/`、`apps/agent_runtime/`、`packages/contracts/`、`infra/compose/` 仅在对应功能开始实现时创建。它们是目标边界，不代表当前已存在的代码或部署服务。

## 4. 分层职责与依赖规则

`src/llmcopy/modules/<context>/` 的分层保持一致：

```text
api → application → domain
                  ↑
           infrastructure
```

| 层 | 可以做什么 | 不得做什么 |
| --- | --- | --- |
| `api` | HTTP/SSE 协议、输入校验、身份依赖、响应映射。 | 承担 RAG 编排、拼接 SQL/Cypher、直接实例化基础设施。 |
| `application` | 用例、事务边界、跨上下文公开 DTO 编排。 | 访问其他模块内部 Repository，或把业务规则塞进 Controller。 |
| `domain` | 聚合、实体、值对象、权限和状态机等纯业务规则。 | 依赖 FastAPI、Pydantic Settings、MySQL、Redis、ES、Neo4j、MinerU 或模型 SDK。 |
| `infrastructure` | 实现 Repository、外部服务和持久化端口。 | 包含角色、车辆范围和问答业务判断。 |

- `identity`、`knowledge`、`vehicle`、`conversation` 不反向依赖 `qa` 或 `agent_runtime`。
- `qa` 只能通过版本化内部 HTTP 契约启动或查询 Agent；不得把 Agent 工作流重新实现到 FastAPI。
- Next.js Web 的 `modules/*/presentation` 只能调用类型化 API/SSE 客户端，不能 import Python 领域模型或访问数据库。
- `shared/` 和 `shared/ui/` 只允许放稳定、无业务归属的能力；任何带“车主、员工、车型、文档、会话、权限”等语义的代码必须回到对应上下文。
- 新增跨上下文输入、输出、SSE 或任务消息时，先更新 `packages/contracts/`，再生成或实现 Python/TypeScript 类型和契约测试。管理员员工访问接口属于 Identity 与 Administration 的受控应用入口，必须保留幂等和审计记录。

## 5. 数据、迁移与测试归属

- Alembic 是 MySQL Schema 的唯一所有者；`resources/sql` 如保留仅作参考，不能作为第二套建表入口。
- Neo4j P0 图模型已确认仅包含公开 `Brand`、`CarModel`、`CarVersion`、`EnergyType`、`Part` 节点及其车型、版本、能源和配件关系；图查询只允许参数化白名单 Cypher。车主绑定仅认 `my_car.user_id`。P0 员工仅区分现有 `staff_info` 中的在职普通员工 `job=staff` 与在职领导 `job=leader`；`dept_id` 可维护但暂不参与知识授权，不新增区域或车型范围字段。
- Elasticsearch 映射、MinIO 对象和向量均为 Knowledge 的可重建投影；文档元数据与处理状态仍由 MySQL 记录。
- 单元测试与模块并列；跨系统集成、RAG 评测、安全越权和契约测试放根目录 `tests/` 的对应分类，避免只验证单个 Adapter 而遗漏三类角色的端到端授权。

## 6. 渐进落地顺序

1. 保持现有 `src/llmcopy` 运行，先在 `packages/contracts/` 定义 `/api/v1`、SSE 和 Agent 内部工具契约。
2. 在车主绑定和 Neo4j 查询边界明确后，逐步填充 `identity`、`vehicle` 和 Neo4j Adapter；先完成权限与越权测试。
3. 按现有目录扩展 `knowledge`、`conversation`、`qa`、`admin`，不一次性移动已经通过测试的实现。
4. 创建 `apps/agent_runtime/`，将意图、改写、检索编排、重排、Prompt 和 Relier Worker 放入其中；通过 ACL 调用 FastAPI 受控工具。
5. 创建 `apps/web/`，实现 Next.js 页面和 SSE 客户端；只消费 `/api/v1` 与类型契约。
6. 在预生产通过 MySQL/ES/MinIO/Neo4j/Redis、DLQ、SSE、三角色越权和沙箱测试后，才允许 Web P0 上线。

## 7. 实施前仍需确认的目录相关决策

| 决策 | 影响 |
| --- | --- |
| 员工账号与权限（已确认） | P0 使用 `staff_info.emp_id`、`status` 与固定岗位值 `staff`、`leader`；普通员工的 `status`、`dept_id` 仅可由在职 `leader` 修改，岗位不可通过该接口修改；变更后由后端立即清除目标员工 Redis Session。 |
| 密码认证与顾客改密（已确认） | 新密码使用 Argon2id（19456 KiB、2 次、1 并行度、16 字节盐、32 字节摘要）；旧 PBKDF2 仅兼容成功登录后的自动升级。顾客修改密码前撤销其全部 Redis Session，写入失败不保留旧登录态。 |
| HR 初始密码开户（已确认） | HR 系统创建 `staff_info` 主数据后调用内部接口设置初始密码；`X-Internal-Service-Token` + `Idempotency-Key` 鉴权，仅允许在职且密码为空的已有员工。接口生成 Argon2id、不创建或修改 HR 主数据、写入前撤销员工 Session；无敏感审计保留六个月，幂等记录保留 24 小时。 |
| 知识审核与检索（已确认） | `staff`、`leader` 可上传，先由 MinerU 解析为 Markdown 并进入 `PENDING_REVIEW`；只有 `leader` 能审核。审核通过时选择 `VISITOR`、`STAFF` 或 `LEADER`，标签写入分片 `metadata.accessibleBy` 后才向量化；驳回文档不进入 ES。领导检索范围包含 `VISITOR`、`STAFF`、`LEADER`。待审核和驳回文档仅上传者及领导可查看。 |
| P0 流式问答（已确认） | 使用 DashScope `qwen3.7-plus`；SSE 先输出 `progress` 和 `query_rewrite`，返回改写前后查询及服务端增强查询上下文，再输出 `answer_delta`、`citation`、`error`、`done`；所有事件含递增 `sequence`、`trace_id`；15 秒心跳、120 秒最长响应、断开即取消且不保存部分回答。每个主体在 Redis 中同时仅允许一条活动流，完整成功后才保存助手回答与引用。 |
| P0 意图识别（已确认） | 在 `modules/qa` 使用 DashScope `qwen3.5-flash` 输出经 Pydantic 严格校验的 JSON。仅有 `related`、七类 `intent`、八项 `entities` 与不超过 200 字的 `reasoning`；禁止生成、传输或记录完整思维链。Schema 或调用失败最多重试一次，仍失败则安全降级为通用对话，且不访问 Elasticsearch 或企业数据。 |
| 父子块检索补全（已确认） | ES 命中后必须回 MySQL 以文档当前 `VECTOR_STORED` 状态与 `accessible_by` 二次授权；短父块（不超过 4000 字符）完整回溯，长父块使用命中子块与前后相邻块窗口补全。基础窗口每侧 1 块；检测到分片边界未结束且有预算时每侧最多扩展到 2 块；全部上下文仍受回答 12000 字符预算约束。 |
| 车主绑定（已确认） | P0 仅以 `my_car.user_id` 作为唯一当前车主依据；不支持共享；后端过户或解绑时更新或清空该字段，并立即撤销原车主 Redis Session。`car_order.user_id` 不单独授予车辆访问权。 |
| Neo4j 版本、部署形态、图空间和同步链路 | 决定 `infrastructure/graph` 的配置、投影任务和查询模板。 |
| Redis 认证/锁与 Celery/Relier Broker 是否物理隔离 | 决定 `infra/redis`、`infra/compose` 与 Agent Runtime 的最小凭据边界。 |
| Next.js 的 UI 组件库、表单方案和部署平台 | 只影响 `apps/web` 的展示层实现，不得影响 FastAPI 的领域边界。 |
