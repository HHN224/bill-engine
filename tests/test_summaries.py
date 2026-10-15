from collections.abc import Generator
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import Mock
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.database import (
    create_database_engine,
    get_db,
    initialize_database,
)
from app.main import app
from app.models import Transaction


ADMIN_AUTH_HEADERS = {"Authorization": "Bearer test-admin-token"}
APP_AUTH_HEADERS = {"Authorization": "Bearer test-app-token"}
TEST_TIMEZONE = ZoneInfo("Asia/Taipei")


@pytest.fixture
def summary_client(
    tmp_path,
) -> Generator[tuple[TestClient, sessionmaker[Session]], None, None]:
    """提供使用独立 SQLite 数据库的统计接口测试客户端。"""
    database_file = tmp_path / "summaries.db"
    engine = create_database_engine(
        f"sqlite:///{database_file.as_posix()}"
    )
    initialize_database(engine)
    testing_session = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )

    def override_get_db() -> Generator[Session, None, None]:
        session = testing_session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_settings] = lambda: Settings(
        app_api_token="test-app-token",
        admin_password="test-admin-token",
        default_timezone="Asia/Taipei",
    )

    with TestClient(app) as client:
        yield client, testing_session

    app.dependency_overrides.clear()
    engine.dispose()


def add_transaction(
    session: Session,
    *,
    transaction_type: str,
    amount: str,
    category: str,
    occurred_at: datetime,
    note: str,
) -> None:
    """向测试数据库写入一笔结构化账单。"""
    session.add(
        Transaction(
            type=transaction_type,
            amount=Decimal(amount),
            currency="CNY",
            category=category,
            subcategory=None,
            merchant=None,
            payment_method=None,
            occurred_at=occurred_at,
            note=note,
            tags=[],
            raw_text=note,
            confidence=Decimal("1"),
        )
    )


def test_daily_summary_uses_current_local_date_and_database_totals(
    summary_client,
) -> None:
    client, testing_session = summary_client
    local_now = datetime.now(TEST_TIMEZONE)
    today_noon = local_now.replace(
        hour=12,
        minute=0,
        second=0,
        microsecond=0,
    )
    with testing_session() as session:
        add_transaction(
            session,
            transaction_type="expense",
            amount="18.50",
            category="餐饮",
            occurred_at=today_noon,
            note="午饭",
        )
        add_transaction(
            session,
            transaction_type="expense",
            amount="20",
            category="交通",
            occurred_at=today_noon + timedelta(hours=1),
            note="打车",
        )
        add_transaction(
            session,
            transaction_type="income",
            amount="100",
            category="收入",
            occurred_at=today_noon + timedelta(hours=2),
            note="退款",
        )
        add_transaction(
            session,
            transaction_type="expense",
            amount="999",
            category="其他",
            occurred_at=today_noon - timedelta(days=1),
            note="昨日支出",
        )
        session.commit()

    response = client.get(
        "/api/summaries/daily",
        headers=ADMIN_AUTH_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["date"] == local_now.date().isoformat()
    assert body["expense_total"] == 38.5
    assert body["income_total"] == 100.0
    assert body["transaction_count"] == 3
    assert body["categories"] == [
        {"category": "交通", "amount": 20.0},
        {"category": "餐饮", "amount": 18.5},
    ]


def test_monthly_summary_calculates_net_categories_and_daily_totals(
    summary_client,
) -> None:
    client, testing_session = summary_client
    with testing_session() as session:
        add_transaction(
            session,
            transaction_type="expense",
            amount="10",
            category="餐饮",
            occurred_at=datetime(
                2026, 7, 1, 0, 30, tzinfo=TEST_TIMEZONE
            ),
            note="早餐",
        )
        add_transaction(
            session,
            transaction_type="expense",
            amount="5",
            category="餐饮",
            occurred_at=datetime(
                2026, 7, 1, 18, 0, tzinfo=TEST_TIMEZONE
            ),
            note="饮料",
        )
        add_transaction(
            session,
            transaction_type="expense",
            amount="20",
            category="交通",
            occurred_at=datetime(
                2026, 7, 2, 9, 0, tzinfo=TEST_TIMEZONE
            ),
            note="打车",
        )
        add_transaction(
            session,
            transaction_type="income",
            amount="50",
            category="收入",
            occurred_at=datetime(
                2026, 7, 3, 10, 0, tzinfo=TEST_TIMEZONE
            ),
            note="退款",
        )
        add_transaction(
            session,
            transaction_type="expense",
            amount="999",
            category="其他",
            occurred_at=datetime(
                2026, 8, 1, 0, 0, tzinfo=TEST_TIMEZONE
            ),
            note="下月支出",
        )
        session.commit()

    response = client.get(
        "/api/summaries/monthly",
        headers=ADMIN_AUTH_HEADERS,
        params={"year": 2026, "month": 7},
    )

    assert response.status_code == 200
    assert response.json() == {
        "year": 2026,
        "month": 7,
        "expense_total": 35.0,
        "income_total": 50.0,
        "net_amount": 15.0,
        "transaction_count": 4,
        "categories": [
            {"category": "交通", "amount": 20.0},
            {"category": "餐饮", "amount": 15.0},
        ],
        "daily_totals": [
            {"date": "2026-07-01", "amount": 15.0},
            {"date": "2026-07-02", "amount": 20.0},
        ],
    }


def test_empty_month_returns_zero_totals(
    summary_client,
) -> None:
    client, _ = summary_client

    response = client.get(
        "/api/summaries/monthly",
        headers=ADMIN_AUTH_HEADERS,
        params={"year": 2025, "month": 1},
    )

    assert response.status_code == 200
    assert response.json()["expense_total"] == 0.0
    assert response.json()["income_total"] == 0.0
    assert response.json()["net_amount"] == 0.0
    assert response.json()["transaction_count"] == 0
    assert response.json()["categories"] == []
    assert response.json()["daily_totals"] == []


def test_daily_summary_allows_app_token(summary_client) -> None:
    client, _ = summary_client

    response = client.get(
        "/api/summaries/daily",
        headers=APP_AUTH_HEADERS,
    )

    assert response.status_code == 200


def test_monthly_summary_rejects_app_token(summary_client) -> None:
    client, _ = summary_client

    response = client.get(
        "/api/summaries/monthly",
        headers=APP_AUTH_HEADERS,
        params={"year": 2026, "month": 7},
    )

    assert response.status_code == 401
    assert response.json()["error_code"] == "UNAUTHORIZED"


def test_summary_endpoints_require_token(summary_client) -> None:
    client, _ = summary_client

    response = client.get(
        "/api/summaries/monthly",
        params={"year": 2026, "month": 7},
    )

    assert response.status_code == 401
    assert response.json()["error_code"] == "UNAUTHORIZED"


def test_invalid_month_returns_unified_validation_error(
    summary_client,
) -> None:
    client, _ = summary_client

    response = client.get(
        "/api/summaries/monthly",
        headers=ADMIN_AUTH_HEADERS,
        params={"year": 2026, "month": 13},
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "VALIDATION_ERROR"


def test_summary_database_error_returns_clear_error_and_safe_log(
    summary_client,
    capfd: pytest.CaptureFixture[str],
) -> None:
    client, _ = summary_client
    error_detail = "summary-database-secret-must-not-be-logged"
    session = Mock(spec=Session)
    session.execute.side_effect = SQLAlchemyError(error_detail)

    def unavailable_database() -> Generator[Session, None, None]:
        yield session

    original_override = app.dependency_overrides[get_db]
    app.dependency_overrides[get_db] = unavailable_database
    try:
        response = client.get(
            "/api/summaries/monthly",
            headers=ADMIN_AUTH_HEADERS,
            params={"year": 2026, "month": 7},
        )
    finally:
        app.dependency_overrides[get_db] = original_override

    assert response.status_code == 500
    assert response.json()["error_code"] == "DATABASE_ERROR"
    captured = capfd.readouterr()
    assert (
        "Summary database query failed error_type=SQLAlchemyError"
        in captured.out
    )
    assert error_detail not in captured.out


def test_invalid_summary_timezone_returns_clear_error(
    summary_client,
) -> None:
    client, _ = summary_client
    app.dependency_overrides[get_settings] = lambda: Settings(
        app_api_token="test-app-token",
        admin_password="test-admin-token",
        default_timezone="Invalid/Timezone",
    )

    response = client.get(
        "/api/summaries/daily",
        headers=ADMIN_AUTH_HEADERS,
    )

    assert response.status_code == 500
    assert response.json()["error_code"] == "INVALID_TIMEZONE_CONFIG"
