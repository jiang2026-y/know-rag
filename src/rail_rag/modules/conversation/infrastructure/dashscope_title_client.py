"""DashScope 标题客户端：用 qwen3.5-flash 为首条用户消息生成简短会话标题。"""

from __future__ import annotations

import re
from typing import Any

import httpx

from rail_rag.config.settings import Settings


class ChatTitleGenerationError(RuntimeError):
    """标题模型未能返回可用标题时抛出。"""


class DashScopeTitleClient:
    """通过 DashScope OpenAI 兼容接口生成不超过限定长度的中文标题。"""

    _SYSTEM_PROMPT = (
        "你是一个对话标题生成助手。根据用户的第一句话，生成一个简洁的中文会话标题，"
        "要求：不超过20个字，不加引号，直接输出标题内容。"
    )

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = str(settings.embedding_base_url).rstrip("/")
        self._api_key = settings.embedding_api_key
        self._model_name = settings.chat_title_model_name
        self._max_length = settings.chat_title_max_length
        self._transport = transport
        self._timeout = httpx.Timeout(
            connect=settings.chat_title_connect_timeout_seconds,
            read=settings.chat_title_response_timeout_seconds,
            write=settings.chat_title_response_timeout_seconds,
            pool=settings.chat_title_connect_timeout_seconds,
        )

    async def generate(self, first_message: str) -> str:
        """调用轻量模型并将输出规范化为可安全持久化的标题。"""
        message = first_message.strip()
        if not message:
            raise ChatTitleGenerationError("First user message must not be empty")
        token = self._api_token()
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                response = await client.post(
                    "/chat/completions",
                    headers={"Authorization": f"Bearer {token}"},
                    json={
                        "model": self._model_name,
                        "messages": [
                            {"role": "system", "content": self._SYSTEM_PROMPT},
                            {"role": "user", "content": message},
                        ],
                        "temperature": 0.1,
                        "max_completion_tokens": 32,
                        "enable_thinking": False,
                        "stream": False,
                    },
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text.strip()[:1000]
            raise ChatTitleGenerationError(
                "Chat title request failed with HTTP "
                f"{exc.response.status_code}: {detail or exc.response.reason_phrase}"
            ) from exc
        except httpx.RequestError as exc:
            raise ChatTitleGenerationError(
                f"Chat title network request failed: {exc}"
            ) from exc
        return self._normalize_title(self._content_from_payload(response.json()))

    def _api_token(self) -> str:
        if self._api_key is None:
            raise ChatTitleGenerationError(
                "EMBEDDING_API_KEY is required for chat title generation"
            )
        token = self._api_key.get_secret_value().strip()
        if not token:
            raise ChatTitleGenerationError(
                "EMBEDDING_API_KEY is required for chat title generation"
            )
        return token

    @staticmethod
    def _content_from_payload(payload: Any) -> str:
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ChatTitleGenerationError(
                "Chat title API returned an invalid response"
            ) from exc
        if not isinstance(content, str):
            raise ChatTitleGenerationError("Chat title API returned a non-text title")
        return content

    def _normalize_title(self, raw_title: str) -> str:
        title = re.sub(r"\s+", " ", raw_title).strip()
        title = title.strip("\"'“”‘’")
        title = title[: self._max_length].strip()
        if not title:
            raise ChatTitleGenerationError("Chat title API returned an empty title")
        return title
