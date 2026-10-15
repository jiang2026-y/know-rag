"""DashScope 意图识别客户端：调用 qwen3.5-flash 并返回原始 JSON 文本。

定义 IntentClient 协议、IntentRecognitionError 和 DashScopeIntentClient；
客户端仅负责模型调用、超时和响应协议校验，领域 Schema 校验由应用服务完成。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

import httpx

from rail_rag.config.settings import Settings


class IntentRecognitionError(RuntimeError):
    """DashScope 未返回可用于意图识别的文本结果时抛出。"""


class IntentClient(Protocol):
    """定义应用服务所需的非流式意图识别能力。"""

    async def recognize(self, question: str, user_role: str, *, retry: bool = False) -> str:
        """针对问题和服务端推导的角色返回一个 JSON 对象文本。"""


class DashScopeIntentClient:
    """通过 DashScope OpenAI 兼容接口调用 qwen3.5-flash 的意图识别客户端。"""

    _PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "intent-recognition-new-prompt.txt"
    _SYSTEM_PROMPT = _PROMPT_PATH.read_text(encoding="utf-8")

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = str(settings.embedding_base_url).rstrip("/")
        self._api_key = settings.embedding_api_key
        self._model_name = settings.chat_intent_model_name
        self._max_completion_tokens = settings.chat_intent_max_completion_tokens
        self._transport = transport
        self._timeout = httpx.Timeout(
            connect=settings.chat_intent_connect_timeout_seconds,
            read=settings.chat_intent_response_timeout_seconds,
            write=settings.chat_intent_response_timeout_seconds,
            pool=settings.chat_intent_connect_timeout_seconds,
        )

    async def recognize(self, question: str, user_role: str, *, retry: bool = False) -> str:
        """调用模型取得 JSON 文本；重试时仅追加 Schema 修正指令。"""
        message = question.strip()
        if not message:
            raise IntentRecognitionError("Question must not be empty")
        token = self._api_token()
        correction = "上一次输出未通过 JSON Schema 校验。请仅返回符合上述输出结构的单个 JSON 对象。" if retry else ""
        prompt = self._SYSTEM_PROMPT.replace("{{query}}", message)
        if correction:
            prompt = f"{prompt}\n{correction}"
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
                            {"role": "system", "content": prompt},
                            {"role": "user", "content": f"服务端可信用户角色：{user_role}"},
                        ],
                        "temperature": 0,
                        "max_completion_tokens": self._max_completion_tokens,
                        "enable_thinking": False,
                        "stream": False,
                    },
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text.strip()[:1000]
            raise IntentRecognitionError(
                "Intent recognition request failed with HTTP "
                f"{exc.response.status_code}: {detail or exc.response.reason_phrase}"
            ) from exc
        except httpx.RequestError as exc:
            raise IntentRecognitionError(f"Intent recognition network request failed: {exc}") from exc
        return self._content_from_payload(response.json())

    def _api_token(self) -> str:
        if self._api_key is None:
            raise IntentRecognitionError("EMBEDDING_API_KEY is required for intent recognition")
        token = self._api_key.get_secret_value().strip()
        if not token:
            raise IntentRecognitionError("EMBEDDING_API_KEY is required for intent recognition")
        return token

    @staticmethod
    def _content_from_payload(payload: Any) -> str:
        """从 OpenAI 兼容响应提取非空的模型文本。"""
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise IntentRecognitionError("Intent recognition API returned an invalid response") from exc
        if not isinstance(content, str) or not content.strip():
            raise IntentRecognitionError("Intent recognition API returned an empty response")
        return content.strip()
