# LLMcopy Agent Runtime

这是独立的 Agent Runtime 服务骨架。它只接收 api_service 转发的可信上下文，通过内部工具契约参与意图识别、检索编排、重排和回答事件组织；禁止直连 MySQL、Elasticsearch、MinIO、Neo4j 或业务 Redis。

当前仓库已先提供健康检查和内部认证入口，完整问答工作流迁移需在内部工具 OpenAPI 契约稳定后进行。
