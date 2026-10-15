"""账单创建、查询、修改和删除接口。"""

from datetime import date, datetime, time, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.dependencies import (
    get_transaction_parser,
    verify_admin_token,
    verify_shortcut_token,
)
from app.errors import APIError
from app.logging_config import get_application_logger
from app.models import Transaction
from app.schemas import (
    DeleteTransactionResponse,
    ManualTransactionCreate,
    ParseAndCreateRequest,
    ParseAndCreateResponse,
    TransactionListResponse,
    TransactionResponse,
    TransactionUpdate,
)
from app.services.llm_client import (
    LLMConfigurationError,
    LLMResponseError,
    LLMServiceError,
    LLMTimeoutError,
)
from app.services.transaction_parser import (
    Category,
    InvalidModelOutputError,
    InvalidParserInputError,
    TransactionParser,
    TransactionType,
)


logger = get_application_logger(__name__)


router = APIRouter(
    prefix="/api/transactions",
    tags=["transactions"],
)


def _transaction_response(transaction: Transaction) -> TransactionResponse:
    """把数据库对象转换为对外响应。"""
    return TransactionResponse.model_validate(transaction)


def _database_error(session: Session, exc: SQLAlchemyError) -> APIError:
    """回滚事务并生成数据库错误。"""
    logger.error(
        "Transaction database operation failed error_type=%s",
        type(exc).__name__,
    )
    session.rollback()
    return APIError(
        status_code=500,
        error_code="DATABASE_ERROR",
        message="The database operation failed.",
    )


def _get_transaction(session: Session, transaction_id: int) -> Transaction:
    """读取单笔账单，不存在时返回明确错误。"""
    try:
        transaction = session.get(Transaction, transaction_id)
    except SQLAlchemyError as exc:
        raise _database_error(session, exc) from exc
    if transaction is None:
        raise APIError(
            status_code=404,
            error_code="TRANSACTION_NOT_FOUND",
            message="The transaction was not found.",
        )
    return transaction


def _timezone_from_settings(settings: Settings) -> ZoneInfo:
    """读取并校验默认时区配置。"""
    try:
        return ZoneInfo(settings.default_timezone)
    except ZoneInfoNotFoundError as exc:
        raise APIError(
            status_code=500,
            error_code="INVALID_TIMEZONE_CONFIG",
            message="DEFAULT_TIMEZONE is invalid.",
        ) from exc


@router.post(
    "/parse-and-create",
    response_model=ParseAndCreateResponse,
    response_model_exclude_none=True,
    dependencies=[Depends(verify_shortcut_token)],
)
def parse_and_create_transaction(
    request: ParseAndCreateRequest,
    session: Annotated[Session, Depends(get_db)],
    parser: Annotated[TransactionParser, Depends(get_transaction_parser)],
) -> ParseAndCreateResponse:
    """解析自然语言，并在金额有效时创建账单。"""
    try:
        parsed = parser.parse(
            request.text,
            request.current_time,
            request.timezone,
        )
    except InvalidParserInputError as exc:
        raise APIError(
            status_code=400,
            error_code="INVALID_PARSER_INPUT",
            message=str(exc),
        ) from exc
    except LLMConfigurationError as exc:
        raise APIError(
            status_code=503,
            error_code="LLM_NOT_CONFIGURED",
            message=str(exc),
        ) from exc
    except LLMTimeoutError as exc:
        raise APIError(
            status_code=504,
            error_code="LLM_TIMEOUT",
            message=str(exc),
        ) from exc
    except LLMServiceError as exc:
        raise APIError(
            status_code=502,
            error_code="LLM_SERVICE_ERROR",
            message=str(exc),
        ) from exc
    except (LLMResponseError, InvalidModelOutputError) as exc:
        raise APIError(
            status_code=502,
            error_code="INVALID_LLM_RESPONSE",
            message=str(exc),
        ) from exc

    if any(item.amount is None for item in parsed):
        return ParseAndCreateResponse(
            success=False,
            requires_confirmation=True,
            message=(
                "没有识别到有效金额，请补充金额。"
                if len(parsed) == 1
                else "部分账单缺少金额，本次全部未保存，请补充后重新提交。"
            ),
            parsed_data=parsed[0] if len(parsed) == 1 else None,
            parsed_items=parsed,
        )

    transactions = [
        Transaction(**item.model_dump(), raw_text=request.text)
        for item in parsed
    ]
    try:
        session.add_all(transactions)
        session.commit()
        for transaction in transactions:
            session.refresh(transaction)
    except SQLAlchemyError as exc:
        raise _database_error(session, exc) from exc

    descriptions = [
        f"{item.category} {item.amount:.2f} 元，"
        f"{item.note or item.subcategory or item.merchant or '无备注'}"
        for item in parsed
    ]
    responses = [_transaction_response(item) for item in transactions]
    return ParseAndCreateResponse(
        success=True,
        requires_confirmation=False,
        message=(
            f"已记录：{descriptions[0]}"
            if len(parsed) == 1
            else f"已记录 {len(parsed)} 笔：" + "；".join(descriptions)
        ),
        transaction=responses[0] if len(responses) == 1 else None,
        transactions=responses,
    )


@router.post(
    "/manual",
    response_model=TransactionResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(verify_admin_token)],
)
def create_manual_transaction(
    request: ManualTransactionCreate,
    session: Annotated[Session, Depends(get_db)],
) -> TransactionResponse:
    """直接保存后台表单提供的结构化交易，不调用大模型。"""
    transaction = Transaction(
        **request.model_dump(),
        raw_text="",
        confidence=None,
    )
    session.add(transaction)
    try:
        session.commit()
        session.refresh(transaction)
    except SQLAlchemyError as exc:
        raise _database_error(session, exc) from exc
    return _transaction_response(transaction)


@router.get(
    "",
    response_model=TransactionListResponse,
    dependencies=[Depends(verify_admin_token)],
)
def list_transactions(
    session: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    start_date: date | None = None,
    end_date: date | None = None,
    category: Category | None = None,
    transaction_type: Annotated[
        TransactionType | None,
        Query(alias="type"),
    ] = None,
    keyword: Annotated[str | None, Query(min_length=1)] = None,
) -> TransactionListResponse:
    """按条件查询账单，并按发生时间倒序返回。"""
    if start_date and end_date and start_date > end_date:
        raise APIError(
            status_code=422,
            error_code="INVALID_DATE_RANGE",
            message="start_date cannot be later than end_date.",
        )

    filters = []
    configured_timezone = _timezone_from_settings(settings)
    if start_date is not None:
        start_time = datetime.combine(
            start_date,
            time.min,
            tzinfo=configured_timezone,
        )
        filters.append(Transaction.occurred_at >= start_time)
    if end_date is not None:
        end_time = datetime.combine(
            end_date + timedelta(days=1),
            time.min,
            tzinfo=configured_timezone,
        )
        filters.append(Transaction.occurred_at < end_time)
    if category is not None:
        filters.append(Transaction.category == category)
    if transaction_type is not None:
        filters.append(Transaction.type == transaction_type)
    if keyword is not None:
        search_term = f"%{keyword.strip()}%"
        filters.append(
            or_(
                Transaction.raw_text.ilike(search_term),
                Transaction.note.ilike(search_term),
                Transaction.merchant.ilike(search_term),
            )
        )

    statement = (
        select(Transaction)
        .where(*filters)
        .order_by(Transaction.occurred_at.desc())
        .offset(offset)
        .limit(limit)
    )
    count_statement = (
        select(func.count())
        .select_from(Transaction)
        .where(*filters)
    )
    try:
        transactions = session.scalars(statement).all()
        total = session.scalar(count_statement) or 0
    except SQLAlchemyError as exc:
        raise _database_error(session, exc) from exc
    return TransactionListResponse(
        items=[_transaction_response(item) for item in transactions],
        total=total,
    )


@router.get(
    "/{transaction_id}",
    response_model=TransactionResponse,
    dependencies=[Depends(verify_admin_token)],
)
def get_transaction(
    transaction_id: int,
    session: Annotated[Session, Depends(get_db)],
) -> TransactionResponse:
    """查询单笔账单。"""
    return _transaction_response(_get_transaction(session, transaction_id))


@router.patch(
    "/{transaction_id}",
    response_model=TransactionResponse,
    dependencies=[Depends(verify_admin_token)],
)
def update_transaction(
    transaction_id: int,
    update: TransactionUpdate,
    session: Annotated[Session, Depends(get_db)],
) -> TransactionResponse:
    """修改允许用户纠正的账单字段。"""
    transaction = _get_transaction(session, transaction_id)
    for field_name, value in update.model_dump(exclude_unset=True).items():
        setattr(transaction, field_name, value)

    try:
        session.commit()
        session.refresh(transaction)
    except SQLAlchemyError as exc:
        raise _database_error(session, exc) from exc
    return _transaction_response(transaction)


@router.delete(
    "/{transaction_id}",
    response_model=DeleteTransactionResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(verify_admin_token)],
)
def delete_transaction(
    transaction_id: int,
    session: Annotated[Session, Depends(get_db)],
) -> DeleteTransactionResponse:
    """永久删除单笔账单。"""
    transaction = _get_transaction(session, transaction_id)
    try:
        session.delete(transaction)
        session.commit()
    except SQLAlchemyError as exc:
        raise _database_error(session, exc) from exc
    return DeleteTransactionResponse(
        success=True,
        message="Transaction deleted.",
    )
