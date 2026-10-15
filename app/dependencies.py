"""可复用的 FastAPI 依赖。"""

import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings, get_settings
from app.services.llm_client import LLMClient
from app.services.transaction_parser import TransactionParser


bearer_scheme = HTTPBearer(auto_error=False)


def _verify_bearer_token(
    credentials: HTTPAuthorizationCredentials | None,
    expected_token: str,
) -> None:
    """以常量时间比较请求中的 Bearer Token。"""
    supplied_token = credentials.credentials if credentials else ""
    token_is_valid = bool(expected_token) and secrets.compare_digest(
        supplied_token,
        expected_token,
    )
    if not token_is_valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API token.",
            headers={"WWW-Authenticate": "Bearer"},
        )


def verify_shortcut_token(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(bearer_scheme)
    ],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    """要求 Bearer Token 与手机快捷指令的 APP_API_TOKEN 一致。"""
    _verify_bearer_token(credentials, settings.app_api_token)


def verify_admin_token(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(bearer_scheme)
    ],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    """要求 Bearer Token 与后台管理专用 ADMIN_PASSWORD 一致。"""
    _verify_bearer_token(credentials, settings.admin_password)


def verify_app_or_admin_token(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(bearer_scheme)
    ],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    """允许 APP_API_TOKEN 或 ADMIN_PASSWORD 访问共享接口。"""
    supplied_token = credentials.credentials if credentials else ""
    token_is_valid = any(
        bool(expected_token)
        and secrets.compare_digest(supplied_token, expected_token)
        for expected_token in (
            settings.app_api_token,
            settings.admin_password,
        )
    )
    if not token_is_valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API token.",
            headers={"WWW-Authenticate": "Bearer"},
        )


def get_transaction_parser(
    settings: Annotated[Settings, Depends(get_settings)],
) -> TransactionParser:
    """按照当前配置创建账单解析器。"""
    return TransactionParser(
        LLMClient(settings),
        default_timezone=settings.default_timezone,
    )
