"""意图识别应用服务：校验模型 JSON、退避重试并输出可审计安全结果。

定义 IntentRecognitionService；它根据可信 Session 角色调用模型，最多补发一次
Schema 修正请求，连续失败时降级为不访问企业知识库的通用对话，并返回识别状态。
"""

from __future__ import annotations

import asyncio
import json

from pydantic import ValidationError

from rail_rag.modules.identity.domain.principal import SessionPrincipal
from rail_rag.modules.qa.domain.intent import (
    IntentRecognitionResult,
    general_conversation_intent,
)
from rail_rag.modules.qa.infrastructure.dashscope_intent_client import IntentClient


class IntentRecognitionService:
    """将不可信模型文本转换为供问答编排安全使用且可追踪的意图结果。"""

    def __init__(self, client: IntentClient, *, retry_delays: tuple[float, ...] = (0.2, 0.5)) -> None:
        self._client = client
        self._retry_delays = retry_delays

    async def recognize(
        self, principal: SessionPrincipal, question: str
    ) -> IntentRecognitionResult:
        """最多尝试三次；连续失败降级为不检索企业知识的通用对话。"""
        user_role = self._user_role(principal)
        last_error_type: str | None = None
        max_attempts = len(self._retry_delays) + 1
        attempts_made = 0
        for attempt in range(1, max_attempts + 1):
            retry = attempt > 1
            attempts_made = attempt
            try:
                raw = await self._client.recognize(question, user_role, retry=retry)
                result = IntentRecognitionResult.model_validate(json.loads(raw))
                return result.model_copy(
                    update={
                        "status": "RETRY_SUCCESS" if retry else "SUCCESS",
                        "attempts": attempt,
                        "error_type": last_error_type,
                    }
                )
            except (json.JSONDecodeError, ValidationError, ValueError, RuntimeError) as exc:
                last_error_type = type(exc).__name__
                if attempt <= len(self._retry_delays) and self._is_retryable(exc):
                    await asyncio.sleep(self._retry_delays[attempt - 1])
                else:
                    break
        result = general_conversation_intent("意图识别暂不可用，已按通用对话处理。")
        return result.model_copy(
            update={
                "status": "FALLBACK",
                "attempts": attempts_made,
                "error_type": last_error_type,
            }
        )

    @staticmethod
    def _is_retryable(error: Exception) -> bool:
        """过滤明显的鉴权和请求参数错误，避免无效重试增加模型成本。"""
        message = str(error).lower()
        return not any(marker in message for marker in ("http 400", "http 401", "http 403"))

    @staticmethod
    def _user_role(principal: SessionPrincipal) -> str:
        """仅从可信 Session 推导模型可见角色，不传递用户自报身份。"""
        if principal.principal_type == "STAFF":
            return principal.job or "staff"
        return "customer"
