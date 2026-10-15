"""DashScope 查询路由客户端：使用 qwen3.5-flash 将查询映射到受控数据源。
定义 QueryRouterClient、QueryRouterError 和 DashScopeQueryRouterClient，负责 Prompt 加载、超时、响应提取和模型错误隔离。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

import httpx

from rail_rag.config.settings import Settings


class QueryRouterError(RuntimeError):
    """查询路由模型不可用或返回内容不符合协议时抛出的错误。"""


class QueryRouterClient(Protocol):
    """定义应用层所需的非流式查询路由能力。"""

    async def route(self, question: str) -> str:
        """返回模型产生的原始 JSON 文本。"""


class DashScopeQueryRouterClient:
    """通过 DashScope OpenAI 兼容接口调用 qwen3.5-flash。"""

    _PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "query-routing-new-prompt.txt"
    _SYSTEM_PROMPT = _PROMPT_PATH.read_text(encoding="utf-8")

    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._base_url = str(settings.embedding_base_url).rstrip("/")
        self._api_key = settings.embedding_api_key
        self._model_name = settings.chat_query_router_model_name
        self._max_completion_tokens = settings.chat_query_router_max_completion_tokens
        self._transport = transport
        self._timeout = httpx.Timeout(
            connect=settings.chat_query_router_connect_timeout_seconds,
            read=settings.chat_query_router_response_timeout_seconds,
            write=settings.chat_query_router_response_timeout_seconds,
            pool=settings.chat_query_router_connect_timeout_seconds,
        )

    async def route(self, question: str) -> str:
        """调用路由模型并返回未经信任的 JSON 文本，由应用层继续校验。"""
        message = question.strip()
        if not message:
            raise QueryRouterError("Question must not be empty")
        token = self._api_token()
        try:
            async with httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout, transport=self._transport) as client:
                response = await client.post(
                    "/chat/completions",
                    headers={"Authorization": f"Bearer {token}"},
                    json={
                        "model": self._model_name,
                        "messages": [
                            {"role": "system", "content": self._SYSTEM_PROMPT.replace("{{query}}", message)},
                            {"role": "user", "content": "请根据系统提示词完成查询路由。"},
                        ],
                        "temperature": 0,
                        "max_completion_tokens": self._max_completion_tokens,
                        "enable_thinking": False,
                        "stream": False,
                    },
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise QueryRouterError(f"Query router request failed with HTTP {exc.response.status_code}") from exc
        except httpx.RequestError as exc:
            raise QueryRouterError(f"Query router network request failed: {exc}") from exc
        return self._content_from_payload(response.json())

    def _api_token(self) -> str:
        if self._api_key is None:
            raise QueryRouterError("EMBEDDING_API_KEY is required for query routing")
        token = self._api_key.get_secret_value().strip()
        if not token:
            raise QueryRouterError("EMBEDDING_API_KEY is required for query routing")
        return token

    @staticmethod
    def _content_from_payload(payload: Any) -> str:
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise QueryRouterError("Query router API returned an invalid response") from exc
        if not isinstance(content, str) or not content.strip():
            raise QueryRouterError("Query router API returned an empty response")
        result = content.strip()
        if result.startswith("```") or len(result) > 2000:
            raise QueryRouterError("Query router API returned an invalid JSON response")
        try:
            json.loads(result)
        except json.JSONDecodeError as exc:
            raise QueryRouterError("Query router API returned invalid JSON") from exc
        return result
