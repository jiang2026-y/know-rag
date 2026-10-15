# Neo4j 本地服务

本目录提供开发环境的 Neo4j 5.x Compose 配置。图数据只包含公开车型、车辆版本、能源类型和配件兼容关系，不包含顾客、员工、订单或车辆绑定数据。

当前镜像为 Neo4j Community Edition，仅用于本地开发和测试。Community Edition 不支持为应用账号强制授予 `reader` 角色；本项目通过固定参数化只读 Cypher 保证应用行为，但该部署不得用于生产或任何敏感图数据。

## 启动

先在项目根目录复制 `.env.example` 为 `.env`，设置 `NEO4J_AUTH=neo4j/管理员强密码`，再执行：

```powershell
docker compose --env-file .env -f infra/neo4j/compose.yaml up -d
```

浏览器访问 `http://127.0.0.1:7474`，Bolt 地址为 `bolt://127.0.0.1:7687`。

应用查询应使用单独的只读账号 `NEO4J_USER`，不要在应用配置中使用 Neo4j 管理员账号。Compose 只向容器传递 `NEO4J_AUTH`，`NEO4J_USER` 与 `NEO4J_PASSWORD` 仅供 FastAPI 读取，避免被 Neo4j 误解析为服务器配置。管理员账号只用于开发初始化和创建只读用户。

管理员首次登录后，应创建与 `.env` 中 `NEO4J_USER`、`NEO4J_PASSWORD` 一致的账号，并授予 Neo4j 内置 `reader` 角色。该账号只用于应用查询；导入测试数据和管理数据库仍使用管理员账号。

## 写入公开测试数据

[`seed/public_vehicle_graph.cypher`](./seed/public_vehicle_graph.cypher) 是根据已确认车型、版本和配件关系整理的非破坏性测试数据。它不清库、不删除约束，且所有记录标记为 `dataStatus=TEST`。

在 Neo4j Browser 中打开该文件并执行，或在容器启动后通过 `cypher-shell` 导入。生产环境不得把该测试数据当作官方价格、配置或配件报价使用。
