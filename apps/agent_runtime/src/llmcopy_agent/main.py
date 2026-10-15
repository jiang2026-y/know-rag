"""Agent Runtime HTTP 入口：仅接收可信上下文并转发受控工具调用。

定义 health_check、run_agent 与 AgentRunRequest/AgentRunResponse；本服务不连接任何业务数据库。
"""

from typing import Any

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field


class AgentRunRequest(BaseModel):
    """由 api_service 转发的最小问答上下文。"""

    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=4000)
    principal_context: dict[str, Any]
    trace_id: str = Field(min_length=1, max_length=128)


class AgentRunResponse(BaseModel):
    """Agent 编排结果；证据必须由后端受控工具返回。"""

    status: str
    trace_id: str
    events: list[dict[str, Any]] = Field(default_factory=list)


app = FastAPI(title="LLMcopy Agent Runtime", version="0.1.0")


@app.get("/health")
async def health_check() -> dict[str, str]:
    """返回 Agent 进程健康状态。"""
    return {"status": "ok"}


@app.post("/internal/v1/agent/run", response_model=AgentRunResponse)
async def run_agent(
    payload: AgentRunRequest,
    x_internal_service_token: str | None = Header(default=None),
) -> AgentRunResponse:
    """拒绝未配置内部凭据的调用，并返回待由后端编排的安全占位结果。"""
    # 真实部署由网关或 api_service 注入服务间认证；Agent 不持有业务数据源凭据。
    if not x_internal_service_token:
        raise HTTPException(status_code=401, detail="Internal service authentication required")
    return AgentRunResponse(status="accepted", trace_id=payload.trace_id)
