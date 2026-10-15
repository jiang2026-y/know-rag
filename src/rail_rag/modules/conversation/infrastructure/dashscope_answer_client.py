"""DashScope 主回答客户端：调用 qwen3.7-plus 并解析 OpenAI 兼容 SSE 增量。

定义 ChatAnswerGenerationError、AnswerClient 协议和 DashScopeAnswerClient；客户端负责
安全构造流式请求、提取文本增量，并在网络或上游协议异常时提供统一错误。
"""

from collections.abc import AsyncIterator
import json
from typing import Any, Protocol

import httpx

from rail_rag.config.settings import Settings


class ChatAnswerGenerationError(RuntimeError):
    """主回答模型无法生成可用流式回答时抛出。"""


class AnswerClient(Protocol):
    """定义应用服务依赖的主回答模型流式调用能力。"""

    async def stream_answer(self, system_prompt: str, user_prompt: str) -> AsyncIterator[str]:
        """按生成顺序返回非空回答文本增量。"""


class DashScopeAnswerClient:
    """通过 DashScope OpenAI 兼容接口调用 qwen3.7-plus。"""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = str(settings.embedding_base_url).rstrip("/")
        self._api_key = settings.embedding_api_key
        self._model_name = settings.chat_answer_model_name
        self._max_completion_tokens = settings.chat_answer_max_completion_tokens
        self._transport = transport
        self._timeout = httpx.Timeout(
            connect=settings.chat_answer_connect_timeout_seconds,
            read=settings.chat_answer_response_timeout_seconds,
            write=settings.chat_answer_response_timeout_seconds,
            pool=settings.chat_answer_connect_timeout_seconds,
        )

    async def stream_answer(self, system_prompt: str, user_prompt: str) -> AsyncIterator[str]:
        """流式调用主回答模型，并逐条解析 OpenAI 兼容 data 事件。"""
        token = self._api_token()
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                async with client.stream(
                    "POST",
                    "/chat/completions",
                    headers={"Authorization": f"Bearer {token}"},
                    json={
                        "model": self._model_name,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        "max_completion_tokens": self._max_completion_tokens,
                        "enable_thinking": False,
                        "stream": True,
                    },
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        raw_data = line.removeprefix("data:").strip()
                        if raw_data == "[DONE]":
                            return
                        delta = self._delta_from_payload(raw_data)
                        if delta:
                            yield delta
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text.strip()[:1000]
            raise ChatAnswerGenerationError(
                "Chat answer request failed with HTTP "
                f"{exc.response.status_code}: {detail or exc.response.reason_phrase}"
            ) from exc
        except httpx.RequestError as exc:
            raise ChatAnswerGenerationError(
                f"Chat answer network request failed: {exc}"
            ) from exc

    def _api_token(self) -> str:
        if self._api_key is None:
            raise ChatAnswerGenerationError(
                "EMBEDDING_API_KEY is required for chat answer generation"
            )
        token = self._api_key.get_secret_value().strip()
        if not token:
            raise ChatAnswerGenerationError(
                "EMBEDDING_API_KEY is required for chat answer generation"
            )
        return token

    @staticmethod
    def _delta_from_payload(raw_data: str) -> str:
        """从单条上游 SSE data JSON 中提取文本增量，忽略角色和用量等非文本事件。"""
        try:
            payload: Any = json.loads(raw_data)
        except json.JSONDecodeError as exc:
            raise ChatAnswerGenerationError("Chat answer API returned an invalid stream event") from exc
        if not isinstance(payload, dict):
            raise ChatAnswerGenerationError("Chat answer API returned an invalid stream event")
        choices = payload.get("choices")
        if choices is None:
            return ""
        if not isinstance(choices, list):
            raise ChatAnswerGenerationError("Chat answer API returned an invalid stream event")
        if not choices:
            return ""
        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            raise ChatAnswerGenerationError("Chat answer API returned an invalid stream event")
        delta = first_choice.get("delta")
        if delta is None:
            return ""
        if not isinstance(delta, dict):
            raise ChatAnswerGenerationError("Chat answer API returned an invalid stream event")
        content = delta.get("content")
        if content is None:
            return ""
        if not isinstance(content, str):
            raise ChatAnswerGenerationError("Chat answer API returned an invalid stream event")
        return content
