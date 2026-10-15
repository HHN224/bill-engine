from collections.abc import Generator
from datetime import datetime
from decimal import Decimal
from unittest.mock import Mock

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.database import (
    create_database_engine,
    get_db,
    initialize_database,
)
from app.dependencies import get_transaction_parser
from app.main import app
from app.models import Transaction
from app.services.llm_client import LLMTimeoutError
from app.services.transaction_parser import ParsedTransaction


SHORTCUT_HEADERS = {"Authorization": "Bearer test-shortcut-token"}
ADMIN_HEADERS = {"Authorization": "Bearer test-admin-token"}


def parsed_transaction(
    *,
    amount: Decimal | None = Decimal("18.50"),
    category: str = "餐饮",
    note: str | None = "牛肉饭",
    occurred_at: str = "2026-07-24T12:20:00+08:00",
    transaction_type: str = "expense",
) -> ParsedTransaction:
    """构造测试所需的模型解析结果。"""
    return ParsedTransaction(
        type=transaction_type,
        amount=amount,
        currency="CNY",
        category=category,
        subcategory="午餐" if category == "餐饮" else None,
        merchant="学校食堂" if category == "餐饮" else None,
        payment_method="微信",
        occurred_at=datetime.fromisoformat(occurred_at),
        note=note,
        tags=["食堂", "午餐"] if category == "餐饮" else [],
        confidence=Decimal("0.95"),
    )


@pytest.fixture
def transaction_client(
    tmp_path,
) -> Generator[tuple[TestClient, Mock, sessionmaker[Session]], None, None]:
    """提供使用独立 SQLite 数据库和 Mock 解析器的测试客户端。"""
    database_file = tmp_path / "transactions.db"
    engine = create_database_engine(
        f"sqlite:///{database_file.as_posix()}"
    )
    initialize_database(engine)
    testing_session = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )
    parser = Mock()

    def override_get_db() -> Generator[Session, None, None]:
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

    with TestClient(app) as client:
        yield client, parser, testing_session

    app.dependency_overrides.clear()
    engine.dispose()


def test_parse_and_create_saves_complete_transaction(
    transaction_client,
) -> None:
    client, parser, testing_session = transaction_client
    parser.parse.return_value = [parsed_transaction()]

    response = client.post(
        "/api/transactions/parse-and-create",
        headers=SHORTCUT_HEADERS,
        json={
            "text": "中午食堂牛肉饭18块5，微信支付",
            "timezone": "Asia/Taipei",
            "current_time": "2026-07-24T12:30:00+08:00",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["requires_confirmation"] is False
    assert body["message"] == "已记录：餐饮 18.50 元，牛肉饭"
    assert body["transaction"]["amount"] == 18.5
    assert body["transaction"]["tags"] == ["食堂", "午餐"]

    with testing_session() as session:
        saved = session.scalar(select(Transaction))
        assert saved is not None
        assert saved.amount == Decimal("18.50")
        assert saved.raw_text == "中午食堂牛肉饭18块5，微信支付"
        assert saved.tags == ["食堂", "午餐"]
        assert saved.occurred_at.tzinfo is not None


def test_manual_create_saves_without_calling_parser(
    transaction_client,
) -> None:
    client, parser, testing_session = transaction_client

    response = client.post(
        "/api/transactions/manual",
        headers=ADMIN_HEADERS,
        json={
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
        },
    )

    assert response.status_code == 201
    assert response.json()["amount"] == 18.5
    assert response.json()["raw_text"] == ""
    assert response.json()["confidence"] is None
    parser.parse.assert_not_called()
    with testing_session() as session:
        saved = session.scalar(select(Transaction))
        assert saved is not None
        assert saved.note == "牛肉饭"
        assert saved.confidence is None


def test_shortcut_token_cannot_use_admin_endpoints(
    transaction_client,
) -> None:
    client, parser, _ = transaction_client

    response = client.post(
        "/api/transactions/manual",
        headers=SHORTCUT_HEADERS,
        json={
            "amount": 18.5,
            "category": "餐饮",
            "occurred_at": "2026-07-24T12:20:00+08:00",
        },
    )

    assert response.status_code == 401
    parser.parse.assert_not_called()


def test_manual_create_rejects_invalid_data(
    transaction_client,
) -> None:
    client, parser, _ = transaction_client

    response = client.post(
        "/api/transactions/manual",
        headers=ADMIN_HEADERS,
        json={
            "amount": 0,
            "category": "餐饮",
            "occurred_at": "2026-07-24T12:20:00",
        },
    )

    assert response.status_code == 422
    parser.parse.assert_not_called()


def test_wrong_token_returns_401_without_calling_parser(
    transaction_client,
) -> None:
    client, parser, _ = transaction_client

    response = client.post(
        "/api/transactions/parse-and-create",
        headers={"Authorization": "Bearer wrong-token"},
        json={"text": "午饭15"},
    )

    assert response.status_code == 401
    assert response.json()["error_code"] == "UNAUTHORIZED"
    parser.parse.assert_not_called()


def test_missing_amount_requires_confirmation_without_saving(
    transaction_client,
) -> None:
    client, parser, testing_session = transaction_client
    parser.parse.return_value = [parsed_transaction(amount=None)]

    response = client.post(
        "/api/transactions/parse-and-create",
        headers=SHORTCUT_HEADERS,
        json={"text": "中午吃了牛肉饭"},
    )

    assert response.status_code == 200
    assert response.json()["success"] is False
    assert response.json()["requires_confirmation"] is True
    assert "amount" not in response.json()["parsed_data"]
    with testing_session() as session:
        assert session.scalar(
            select(func.count()).select_from(Transaction)
        ) == 0


def test_llm_timeout_returns_clear_error_without_saving(
    transaction_client,
) -> None:
    client, parser, testing_session = transaction_client
    parser.parse.side_effect = LLMTimeoutError("The LLM request timed out.")

    response = client.post(
        "/api/transactions/parse-and-create",
        headers=SHORTCUT_HEADERS,
        json={"text": "午饭15"},
    )

    assert response.status_code == 504
    assert response.json()["error_code"] == "LLM_TIMEOUT"
    with testing_session() as session:
        assert session.scalar(
            select(func.count()).select_from(Transaction)
        ) == 0


def test_empty_text_returns_unified_validation_error(
    transaction_client,
) -> None:
    client, parser, _ = transaction_client

    response = client.post(
        "/api/transactions/parse-and-create",
        headers=SHORTCUT_HEADERS,
        json={"text": "   "},
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "VALIDATION_ERROR"
    parser.parse.assert_not_called()


def test_list_transactions_supports_order_and_filters(
    transaction_client,
) -> None:
    client, parser, _ = transaction_client
    parser.parse.side_effect = [
        [parsed_transaction(
            amount=Decimal("23"),
            category="交通",
            note="打车",
            occurred_at="2026-07-23T09:00:00+08:00",
        )],
        [parsed_transaction(
            amount=Decimal("18.5"),
            note="牛肉饭",
            occurred_at="2026-07-24T12:20:00+08:00",
        )],
    ]
    for text in ("昨天打车23块", "今天食堂牛肉饭18块5"):
        create_response = client.post(
            "/api/transactions/parse-and-create",
            headers=SHORTCUT_HEADERS,
            json={"text": text},
        )
        assert create_response.status_code == 200

    response = client.get(
        "/api/transactions",
        headers=ADMIN_HEADERS,
    )
    paged = client.get(
        "/api/transactions",
        headers=ADMIN_HEADERS,
        params={"limit": 1, "offset": 0},
    )
    filtered = client.get(
        "/api/transactions",
        headers=ADMIN_HEADERS,
        params={
            "start_date": "2026-07-24",
            "end_date": "2026-07-24",
            "category": "餐饮",
            "type": "expense",
            "keyword": "牛肉",
        },
    )

    assert response.status_code == 200
    assert response.json()["total"] == 2
    assert paged.status_code == 200
    assert paged.json()["total"] == 2
    assert len(paged.json()["items"]) == 1
    assert [item["note"] for item in response.json()["items"]] == [
        "牛肉饭",
        "打车",
    ]
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 1
    assert len(filtered.json()["items"]) == 1
    assert filtered.json()["items"][0]["category"] == "餐饮"


def test_get_update_and_delete_transaction(
    transaction_client,
) -> None:
    client, parser, _ = transaction_client
    parser.parse.return_value = [parsed_transaction()]
    created = client.post(
        "/api/transactions/parse-and-create",
        headers=SHORTCUT_HEADERS,
        json={"text": "午饭18块5"},
    ).json()["transaction"]
    transaction_id = created["id"]

    fetched = client.get(
        f"/api/transactions/{transaction_id}",
        headers=ADMIN_HEADERS,
    )
    updated = client.patch(
        f"/api/transactions/{transaction_id}",
        headers=ADMIN_HEADERS,
        json={
            "type": "income",
            "amount": 20.25,
            "subcategory": None,
            "category": "收入",
            "note": "修正后的备注",
            "merchant": None,
            "payment_method": "现金",
            "tags": ["修正"],
            "occurred_at": "2026-07-24T13:00:00+08:00",
        },
    )
    deleted = client.delete(
        f"/api/transactions/{transaction_id}",
        headers=ADMIN_HEADERS,
    )
    missing = client.get(
        f"/api/transactions/{transaction_id}",
        headers=ADMIN_HEADERS,
    )

    assert fetched.status_code == 200
    assert updated.status_code == 200
    assert updated.json()["type"] == "income"
    assert updated.json()["amount"] == 20.25
    assert updated.json()["subcategory"] is None
    assert updated.json()["category"] == "收入"
    assert updated.json()["merchant"] is None
    assert updated.json()["tags"] == ["修正"]
    assert deleted.status_code == 200
    assert deleted.json()["success"] is True
    assert missing.status_code == 404
    assert missing.json()["error_code"] == "TRANSACTION_NOT_FOUND"


def test_database_error_returns_clear_error_and_safe_log(
    transaction_client,
    capfd: pytest.CaptureFixture[str],
) -> None:
    client, _, _ = transaction_client
    error_detail = "transaction-database-secret-must-not-be-logged"
    session = Mock(spec=Session)
    session.get.side_effect = SQLAlchemyError(error_detail)

    def unavailable_database() -> Generator[Session, None, None]:
        yield session

    original_override = app.dependency_overrides[get_db]
    app.dependency_overrides[get_db] = unavailable_database
    try:
        response = client.get(
            "/api/transactions/1",
            headers=ADMIN_HEADERS,
        )
    finally:
        app.dependency_overrides[get_db] = original_override

    assert response.status_code == 500
    assert response.json()["error_code"] == "DATABASE_ERROR"
    captured = capfd.readouterr()
    assert (
        "Transaction database operation failed error_type=SQLAlchemyError"
        in captured.out
    )
    assert error_detail not in captured.out


def test_update_rejects_invalid_amount_and_empty_body(
    transaction_client,
) -> None:
    client, parser, _ = transaction_client
    parser.parse.return_value = [parsed_transaction()]
    transaction_id = client.post(
        "/api/transactions/parse-and-create",
        headers=SHORTCUT_HEADERS,
        json={"text": "午饭18块5"},
    ).json()["transaction"]["id"]

    invalid_amount = client.patch(
        f"/api/transactions/{transaction_id}",
        headers=ADMIN_HEADERS,
        json={"amount": 0},
    )
    empty_update = client.patch(
        f"/api/transactions/{transaction_id}",
        headers=ADMIN_HEADERS,
        json={},
    )

    invalid_type = client.patch(
        f"/api/transactions/{transaction_id}",
        headers=ADMIN_HEADERS,
        json={"type": "transfer"},
    )
    null_type = client.patch(
        f"/api/transactions/{transaction_id}",
        headers=ADMIN_HEADERS,
        json={"type": None},
    )

    assert invalid_amount.status_code == 422
    assert empty_update.status_code == 422
    assert invalid_type.status_code == 422
    assert null_type.status_code == 422


def test_batch_is_saved_as_separate_rows_and_summarized(transaction_client) -> None:
    client, parser, testing_session = transaction_client
    parser.parse.return_value = [
        parsed_transaction(amount=Decimal("23"), note="午饭"),
        parsed_transaction(amount=Decimal("30"), note="晚饭"),
        parsed_transaction(amount=Decimal("33"), category="购物", note="笔记本"),
    ]
    response = client.post(
        "/api/transactions/parse-and-create",
        headers=SHORTCUT_HEADERS,
        json={"text": "午饭23，晚饭30，笔记本33"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["message"].startswith("已记录 3 笔：")
    assert "transaction" not in body
    assert [item["amount"] for item in body["transactions"]] == [23, 30, 33]
    assert len({item["id"] for item in body["transactions"]}) == 3
    with testing_session() as session:
        saved = session.scalars(select(Transaction).order_by(Transaction.id)).all()
        assert [item.note for item in saved] == ["午饭", "晚饭", "笔记本"]
        assert all(item.raw_text == "午饭23，晚饭30，笔记本33" for item in saved)
    monthly = client.get(
        "/api/summaries/monthly?year=2026&month=7", headers=ADMIN_HEADERS,
    ).json()
    assert monthly["expense_total"] == 86
    assert monthly["transaction_count"] == 3


def test_batch_with_missing_amount_saves_nothing(transaction_client) -> None:
    client, parser, testing_session = transaction_client
    parser.parse.return_value = [
        parsed_transaction(amount=Decimal("23")),
        parsed_transaction(amount=None),
    ]
    response = client.post(
        "/api/transactions/parse-and-create",
        headers=SHORTCUT_HEADERS,
        json={"text": "午饭23，晚饭忘了多少钱"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert body["requires_confirmation"] is True
    assert body["transactions"] == []
    assert len(body["parsed_items"]) == 2
    with testing_session() as session:
        assert session.scalar(select(func.count()).select_from(Transaction)) == 0


def test_batch_database_failure_rolls_back_all_new_rows(transaction_client) -> None:
    client, parser, testing_session = transaction_client
    # 先保存旧账单，再让数据库在写入批次第二笔时失败。
    parser.parse.return_value = [parsed_transaction(amount=Decimal("10"))]
    assert client.post(
        "/api/transactions/parse-and-create", headers=SHORTCUT_HEADERS,
        json={"text": "旧账单10"},
    ).status_code == 200
    with testing_session() as session:
        session.execute(text("""
            CREATE TRIGGER fail_second_bill BEFORE INSERT ON transactions
            WHEN NEW.amount = 30
            BEGIN SELECT RAISE(ABORT, 'test insert failure'); END
        """))
        session.commit()
    parser.parse.return_value = [
        parsed_transaction(amount=Decimal("23")),
        parsed_transaction(amount=Decimal("30")),
    ]
    response = client.post(
        "/api/transactions/parse-and-create", headers=SHORTCUT_HEADERS,
        json={"text": "午饭23，晚饭30"},
    )

    assert response.status_code == 500
    assert response.json()["error_code"] == "DATABASE_ERROR"
    with testing_session() as session:
        saved = session.scalars(select(Transaction)).all()
        assert len(saved) == 1
        assert saved[0].amount == Decimal("10")
