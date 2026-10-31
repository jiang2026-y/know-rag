# 评测专用 Docker 环境

本目录提供与当前开发环境完全隔离的 RAG 评测基础设施。服务使用独立 Docker network、独立数据卷和独立宿主端口，不读取或覆盖 `infra/redis`、`infra/neo4j` 或现有数据库。

## 启动

```powershell
Copy-Item .env.example .env
docker compose --env-file .env up -d
docker compose --env-file .env ps
```

端口如下：

| 服务 | 地址 |
|---|---|
| Elasticsearch | `http://127.0.0.1:19200` |
| MySQL | `127.0.0.1:13306` |
| MinIO API/Console | `127.0.0.1:19000` / `127.0.0.1:19001` |
| Redis | `127.0.0.1:16379` |
| Task Redis | `127.0.0.1:16380` |
| Neo4j HTTP/Bolt | `127.0.0.1:17474` / `127.0.0.1:17687` |

## 停止与清理

```powershell
docker compose --env-file .env stop
docker compose --env-file .env down
```

`down` 不会删除数据卷。只有明确执行 `down -v` 才会删除 `llmcopy-eval-*` 评测卷，请确认目标后再操作。

## 连接当前项目评测脚本

启动隔离服务后，当前项目根目录 `.env` 需要临时指向下列宿主端口；不要把评测密码提交到仓库：

```env
MYSQL_HOST=127.0.0.1
MYSQL_PORT=13306
MYSQL_DATABASE=know_engine_eval
MYSQL_USER=know_engine_eval
MYSQL_PASSWORD=eval_mysql_password
ELASTICSEARCH_HOST=http://127.0.0.1:19200
REDIS_HOST=127.0.0.1
REDIS_PORT=16379
REDIS_PASSWORD=eval_redis_password
NEO4J_URI=bolt://127.0.0.1:17687
NEO4J_USER=neo4j
NEO4J_PASSWORD=eval_neo4j_password
NEO4J_ENABLED=true
```

然后在项目根目录执行 `alembic upgrade head`，并按 `evals/README.md` 导入 MinerU 文档。真实评测还必须配置可用的
`CHAT_RERANK_BASE_URL` 与 `CHAT_RERANK_API_KEY`；采集器会在调用模型前检查这些条件。
