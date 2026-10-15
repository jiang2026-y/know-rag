# LLMcopy 简洁 Web 页面

此目录是最小的 Next.js App Router 前端，提供顾客/员工登录、创建会话、SSE 流式回答、引用展示，以及员工文档上传和领导审核。

浏览器只访问同源的 `/backend/*` 路径；Next.js 将其代理到 FastAPI 的 `/api/v1`，因此 Redis Session Cookie 不需要暴露给跨域前端，也不在前端保存数据库或模型凭据。

## 本地运行

1. 先启动 FastAPI：`uvicorn llmcopy.main:app --reload --port 8001`。
2. 安装 Node.js 20 LTS 或更高版本。
3. 在本目录复制 `.env.example` 为 `.env.local`；FastAPI 不在本机时修改 `BACKEND_API_URL`。
4. 安装并启动：

```powershell
npm install
npm run dev
```

访问 `http://localhost:3000`。登录成功后，浏览器保存由 FastAPI 通过同源代理下发的 HttpOnly Session Cookie。

## 当前边界

- 已实现：顾客/员工账号密码登录、登出、首轮会话、后续提问、SSE 增量回答、引用与错误展示。即使代理在单次网络读取中带回多个 SSE 事件，页面也会逐个动画帧渲染，并用闪烁光标标识正在生成。
- 已实现：员工上传 PDF、DOC、DOCX、CSV、XLS、XLSX，并在上传时选择切片策略（`TITLE`、`SMART`、`LENGTH`、`REGEX`、`SEPARATOR`）与分片字符数、重叠字符数、标题层级、分隔符或正则；留空的参数由服务端按 `TITLE` + 1000 字符 / 100 重叠的默认值处理，非法参数由服务端返回 422。上传后可查看单次任务状态并重试失败任务；普通员工仅查看本人待审核或已驳回文档，领导可查看全部待审核文档并发布为 `VISITOR`、`STAFF` 或 `LEADER`，或者驳回。
- 已实现：完整助手回答下可提交“赞”或“踩”。浏览器仅传递评价值和一次性幂等键；服务端校验回答归属并保存引用、权限与模型快照。用户可以修改同一回答的评价，最近一次评价起保留一年。
- 当前仍待完善：会话列表和历史页面的交互、文档内容预览页面；API 请求已切换到 `/api/v1`，旧 `/api/*` 仍由后端兼容。
- 此页面不直连 MySQL、Redis、Elasticsearch、MinIO、Neo4j 或 DashScope。
