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
