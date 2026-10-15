"""HR 内部开户接口测试：验证服务令牌配置、正确令牌和错误令牌的边界。

定义三项测试，确保未配置时接口不可用，且仅精确匹配的内部服务令牌可通过验证。
"""

import pytest
from fastapi import HTTPException
from pydantic import SecretStr

from rail_rag.config.settings import Settings
from rail_rag.modules.identity.api.internal_staff_provision_controller import (
    verify_hr_service_token,
)


@pytest.mark.asyncio
async def test_hr_token_accepts_exact_configured_value() -> None:
    """配置令牌与请求头一致时允许内部调用。"""
    await verify_hr_service_token("internal-token", Settings(internal_hr_service_token=SecretStr("internal-token")))


@pytest.mark.asyncio
async def test_hr_token_rejects_missing_configuration() -> None:
    """未配置令牌时保持接口不可用。"""
    with pytest.raises(HTTPException) as error:
        await verify_hr_service_token("anything", Settings(_env_file=None))
    assert error.value.status_code == 503


@pytest.mark.asyncio
async def test_hr_token_rejects_wrong_value() -> None:
    """错误令牌不得获得内部开户权限。"""
    with pytest.raises(HTTPException) as error:
        await verify_hr_service_token("wrong", Settings(internal_hr_service_token=SecretStr("correct")))
    assert error.value.status_code == 401
