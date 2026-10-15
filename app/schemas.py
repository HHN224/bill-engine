"""API 对外返回的 Pydantic 数据结构。"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from app.services.transaction_parser import (
    Category,
    ParsedTransaction,
    PositiveAmount,
)


class HealthResponse(BaseModel):
    """健康检查响应。"""

    status: Literal["ok"]


class ParseAndCreateRequest(BaseModel):
    """自然语言解析并创建账单的请求。"""

    text: str
    timezone: str | None = None
    current_time: datetime | None = None

    @field_validator("text")
    @classmethod
    def text_must_not_be_blank(cls, value: str) -> str:
        """拒绝空白记账文本，同时保留原始输入。"""
        if not value.strip():
            raise ValueError("text cannot be empty.")
        return value


class TransactionResponse(BaseModel):
    """对外返回的完整账单。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    type: Literal["expense", "income"]
    amount: Decimal
    currency: str
    category: Category
    subcategory: str | None
    merchant: str | None
    payment_method: str | None
    occurred_at: datetime
    note: str | None
    tags: list[str]
    raw_text: str
    confidence: Decimal | None
    created_at: datetime
    updated_at: datetime

    @field_serializer("amount", "confidence", when_used="json")
    def serialize_decimal(
        self,
        value: Decimal | None,
    ) -> float | None:
        """在 JSON 边界把 Decimal 输出为数字。"""
        return float(value) if value is not None else None


class ParseAndCreateResponse(BaseModel):
    """解析并创建账单的成功或待确认响应。"""

    success: bool
    requires_confirmation: bool
    message: str
    transaction: TransactionResponse | None = None
    parsed_data: ParsedTransaction | None = None
    transactions: list[TransactionResponse] = Field(default_factory=list)
    parsed_items: list[ParsedTransaction] | None = None


class ManualTransactionCreate(BaseModel):
    """后台表单直接创建交易的请求，不经过大模型。"""

    model_config = ConfigDict(extra="forbid")

    type: Literal["expense", "income"] = "expense"
    amount: PositiveAmount
    currency: str = Field(default="CNY", pattern=r"^[A-Z]{3}$")
    category: Category
    subcategory: str | None = Field(default=None, max_length=64)
    merchant: str | None = Field(default=None, max_length=128)
    payment_method: str | None = Field(default=None, max_length=64)
    occurred_at: datetime
    note: str | None = Field(default=None, max_length=255)
    tags: list[str] = Field(default_factory=list)

    @field_validator("occurred_at")
    @classmethod
    def occurred_at_must_include_timezone(
        cls,
        value: datetime,
    ) -> datetime:
        """手工交易也必须携带明确时区，避免服务器时区造成日期偏移。"""
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                "occurred_at must include a timezone offset."
            )
        return value


class TransactionUpdate(BaseModel):
    """用户可以修正的账单字段。"""

    type: Literal["expense", "income"] | None = None
    amount: PositiveAmount | None = None
    category: Category | None = None
    subcategory: str | None = Field(default=None, max_length=64)
    occurred_at: datetime | None = None
    merchant: str | None = None
    note: str | None = None
    payment_method: str | None = None
    tags: list[str] | None = None

    @field_validator("occurred_at")
    @classmethod
    def occurred_at_must_include_timezone(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        """账单发生时间必须包含明确的时区偏移。"""
        if (
            value is not None
            and (value.tzinfo is None or value.utcoffset() is None)
        ):
            raise ValueError(
                "occurred_at must include a timezone offset."
            )
        return value

    @model_validator(mode="after")
    def validate_update_fields(self) -> "TransactionUpdate":
        """拒绝空修改和不允许置空的字段。"""
        if not self.model_fields_set:
            raise ValueError("At least one field must be provided.")
        required_values = {
            "type",
            "amount",
            "category",
            "occurred_at",
            "tags",
        }
        for field_name in self.model_fields_set & required_values:
            if getattr(self, field_name) is None:
                raise ValueError(f"{field_name} cannot be null.")
        return self


class DeleteTransactionResponse(BaseModel):
    """删除账单响应。"""

    success: Literal[True]
    message: str


class TransactionListResponse(BaseModel):
    """后台分页列表及当前筛选条件下的总条数。"""

    items: list[TransactionResponse]
    total: int


class ErrorResponse(BaseModel):
    """统一错误响应。"""

    success: Literal[False]
    error_code: str
    message: str
    details: Any = None


class CategoryAmount(BaseModel):
    """单个支出分类的汇总金额。"""

    category: Category
    amount: Decimal

    @field_serializer("amount", when_used="json")
    def serialize_amount(self, value: Decimal) -> float:
        """把分类金额输出为 JSON 数字。"""
        return float(value)


class DailyAmount(BaseModel):
    """单个日期的支出汇总金额。"""

    date: date
    amount: Decimal

    @field_serializer("amount", when_used="json")
    def serialize_amount(self, value: Decimal) -> float:
        """把每日金额输出为 JSON 数字。"""
        return float(value)


class DailySummaryResponse(BaseModel):
    """今日收支与支出分类汇总。"""

    date: date
    expense_total: Decimal
    income_total: Decimal
    transaction_count: int
    categories: list[CategoryAmount]

    @field_serializer(
        "expense_total",
        "income_total",
        when_used="json",
    )
    def serialize_totals(self, value: Decimal) -> float:
        """把今日汇总金额输出为 JSON 数字。"""
        return float(value)


class MonthlySummaryResponse(BaseModel):
    """月度收支、分类与每日支出汇总。"""

    year: int
    month: int
    expense_total: Decimal
    income_total: Decimal
    net_amount: Decimal
    transaction_count: int
    categories: list[CategoryAmount]
    daily_totals: list[DailyAmount]

    @field_serializer(
        "expense_total",
        "income_total",
        "net_amount",
        when_used="json",
    )
    def serialize_totals(self, value: Decimal) -> float:
        """把月度汇总金额输出为 JSON 数字。"""
        return float(value)
