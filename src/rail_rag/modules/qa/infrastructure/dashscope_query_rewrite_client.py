"""DashScope 查询改写客户端：使用 qwen3.5-flash 输出单一检索查询。

定义 QueryRewriteClient 协议、QueryRewriteError 和 DashScopeQueryRewriteClient；
客户端负责超时、鉴权和响应协议检查，不记录原始 Prompt 或模型思维过程。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

import httpx

from rail_rag.config.settings import Settings


class QueryRewriteError(RuntimeError):
    """DashScope 未返回有效查询改写结果时抛出。"""


class QueryRewriteClient(Protocol):
    """定义应用层所需的非流式查询改写能力。"""

    async def rewrite(self, question: str) -> str:
        """将汽车问题改写为适合检索的单一查询。"""


class DashScopeQueryRewriteClient:
    """通过 DashScope OpenAI 兼容接口调用 qwen3.5-flash。"""

    _PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "query-rewrite-new-prompt.txt"
    _SYSTEM_PROMPT = _PROMPT_PATH.read_text(encoding="utf-8")

    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._base_url = str(settings.embedding_base_url).rstrip("/")
        self._api_key = settings.embedding_api_key
        self._model_name = settings.chat_query_rewrite_model_name
        self._max_completion_tokens = settings.chat_query_rewrite_max_completion_tokens
        self._transport = transport
        self._timeout = httpx.Timeout(
            connect=settings.chat_query_rewrite_connect_timeout_seconds,
            read=settings.chat_query_rewrite_response_timeout_seconds,
            write=settings.chat_query_rewrite_response_timeout_seconds,
            pool=settings.chat_query_rewrite_connect_timeout_seconds,
        )

    async def rewrite(self, question: str) -> str:
        """请求模型生成单一检索查询。"""
        message = question.strip()
        if not message:
            raise QueryRewriteError("Question must not be empty")
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
                            {"role": "user", "content": "请根据系统提示词改写用户查询。"},
                        ],
                        "temperature": 0,
                        "max_completion_tokens": self._max_completion_tokens,
                        "enable_thinking": False,
                        "stream": False,
                    },
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise QueryRewriteError(f"Query rewrite request failed with HTTP {exc.response.status_code}") from exc
        except httpx.RequestError as exc:
            raise QueryRewriteError(f"Query rewrite network request failed: {exc}") from exc
        return self._content_from_payload(response.json())

    def _api_token(self) -> str:
        if self._api_key is None:
            raise QueryRewriteError("EMBEDDING_API_KEY is required for query rewrite")
        token = self._api_key.get_secret_value().strip()
        if not token:
            raise QueryRewriteError("EMBEDDING_API_KEY is required for query rewrite")
        return token

    @staticmethod
    def _content_from_payload(payload: Any) -> str:
        """从 OpenAI 兼容响应中提取有限长度的非空文本。"""
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise QueryRewriteError("Query rewrite API returned an invalid response") from exc
        if not isinstance(content, str) or not content.strip():
            raise QueryRewriteError("Query rewrite API returned an empty response")
        result = content.strip().strip('"').strip()
        if not result or len(result) > 1000 or "```" in result:
            raise QueryRewriteError("Query rewrite API returned an invalid query")
        return result
