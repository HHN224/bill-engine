from collections.abc import Generator
from datetime import datetime
import json

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.database import (
    create_database_engine,
    get_db,
    initialize_database,
)
from app.dependencies import get_transaction_parser
from app.main import app
from app.services.transaction_parser import TransactionParser


SHORTCUT_HEADERS = {"Authorization": "Bearer test-shortcut-token"}
ADMIN_HEADERS = {"Authorization": "Bearer test-admin-token"}


class FakeCompletionClient:
    """返回固定 JSON，用于验证真实解析流程而不消耗模型额度。"""

    def __init__(self) -> None:
        self.call_count = 0
        self.last_user_prompt = ""

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """记录提示词并返回一条符合约束的模型结果。"""
        assert "只返回 JSON" in system_prompt
        self.call_count += 1
        self.last_user_prompt = user_prompt
        return json.dumps(
            {
                "type": "expense",
                "amount": 18.5,
                "currency": "CNY",
                "category": "餐饮",
                "subcategory": "午餐",
                "merchant": "学校食堂",
                "payment_method": "微信",
                "occurred_at": "2026-07-24T12:20:00+08:00",
                "note": "牛肉饭",
                "tags": ["食堂", "午餐"],
                "confidence": 0.95,
            },
            ensure_ascii=False,
        )


def test_shortcut_request_is_parsed_saved_and_summarized(tmp_path) -> None:
    """验证快捷指令请求从解析、入库到查询统计的完整链路。"""
    database_file = tmp_path / "end-to-end.db"
    engine = create_database_engine(
        f"sqlite:///{database_file.as_posix()}"
    )
    initialize_database(engine)
    testing_session = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )
    completion_client = FakeCompletionClient()
    parser = TransactionParser(
        completion_client,
        default_timezone="Asia/Taipei",
    )

    def override_get_db() -> Generator[Session, None, None]:
        """为完整链路测试提供隔离的数据库会话。"""
        session = testing_session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_transaction_parser] = lambda: parser
    app.dependency_overrides[get_settings] = lambda: Settings(
        app_api_token="test-shortcut-token",
        admin_password="test-admin-token",
        default_timezone="Asia/Taipei",
    )

    try:
        with TestClient(app) as client:
            created = client.post(
                "/api/transactions/parse-and-create",
                headers=SHORTCUT_HEADERS,
                json={
                    "text": "中午食堂牛肉饭18块5，微信支付",
                    "timezone": "Asia/Taipei",
                    "current_time": "2026-07-24T12:30:00+08:00",
                },
            )
            recent = client.get(
                "/api/transactions?limit=20",
                headers=ADMIN_HEADERS,
            )
            monthly = client.get(
                "/api/summaries/monthly?year=2026&month=7",
                headers=ADMIN_HEADERS,
            )
    finally:
        app.dependency_overrides.clear()
        engine.dispose()

    assert created.status_code == 200
    assert created.json()["success"] is True
    assert created.json()["message"] == "已记录：餐饮 18.50 元，牛肉饭"
    assert recent.status_code == 200
    assert recent.json()["total"] == 1
    assert len(recent.json()["items"]) == 1
    assert recent.json()["items"][0]["raw_text"] == (
        "中午食堂牛肉饭18块5，微信支付"
    )
    assert monthly.status_code == 200
    assert monthly.json()["expense_total"] == 18.5
    assert monthly.json()["transaction_count"] == 1
    assert completion_client.call_count == 1
    assert "时区：Asia/Taipei" in completion_client.last_user_prompt
