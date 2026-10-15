"""意图识别领域模型：定义七类汽车意图、八项实体和可审计识别状态。

定义 IntentEntities、IntentRecognitionResult 和 GeneralConversationIntent，
分别用于承载实体、校验模型 JSON 结果，以及构造安全降级结果。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


IntentName = Literal[
    "售前咨询与购买",
    "售后维修与保养",
    "车辆使用与技术指导",
    "投诉与维权",
    "汽车营销政策",
    "闲聊与通用问答",
    "其他",
]
IntentRecognitionStatus = Literal["SUCCESS", "RETRY_SUCCESS", "FALLBACK"]


class IntentEntities(BaseModel):
    """承载用户问题中可提取的汽车业务实体；未提及时必须为 null。"""

    model_config = ConfigDict(extra="forbid")

    car_model: str | None = Field(default=None, max_length=128)
    order_id: str | None = Field(default=None, max_length=128)
    dealer: str | None = Field(default=None, max_length=256)
    fault_description: str | None = Field(default=None, max_length=512)
    appointment_time: str | None = Field(default=None, max_length=128)
    part_name: str | None = Field(default=None, max_length=128)
    function_name: str | None = Field(default=None, max_length=128)
    warning_light: str | None = Field(default=None, max_length=128)


class IntentRecognitionResult(BaseModel):
    """经 Schema 校验后的意图结果，并携带有限的识别状态审计字段。"""

    model_config = ConfigDict(extra="forbid")

    related: bool
    intent: IntentName
    entities: IntentEntities
    reasoning: str = Field(min_length=1, max_length=200)
    status: IntentRecognitionStatus = "SUCCESS"
    attempts: int = Field(default=1, ge=1, le=3)
    error_type: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def validate_non_automotive_result(self) -> "IntentRecognitionResult":
        """确保非汽车问题只能进入通用对话，且不会携带业务实体。"""
        if not self.related:
            if self.intent != "闲聊与通用问答":
                raise ValueError("Non-automotive input must use the general conversation intent")
            if any(value is not None for value in self.entities.model_dump().values()):
                raise ValueError("Non-automotive input must not include automotive entities")
        return self


def general_conversation_intent(reasoning: str) -> IntentRecognitionResult:
    """创建识别故障时使用的安全通用对话结果，禁止其触发企业知识检索。"""
    return IntentRecognitionResult(
        related=False,
        intent="闲聊与通用问答",
        entities=IntentEntities(),
        reasoning=reasoning,
    )
