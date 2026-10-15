# LLMcopy 外部契约

本目录保存跨模块、跨服务使用的稳定 HTTP 与 SSE 契约。当前定义的
`sse/chat-message-stream-events.schema.json` 描述 P0 问答流中的四类事件。

任何客户端和服务端修改 SSE 字段前，必须先更新该 Schema 与对应契约测试。
