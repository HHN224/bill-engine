"""把自然语言转换为结构化账单数据。"""

from datetime import datetime
from decimal import Decimal
import json
import re
from typing import Annotated, Literal, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)

from app.config import get_settings
from app.logging_config import get_application_logger


_MAX_LOGGED_RESPONSE_CHARS = 500
logger = get_application_logger(__name__)

TransactionType = Literal["expense", "income"]
Category = Literal[
    "餐饮",
    "交通",
    "购物",
    "娱乐",
    "学习",
    "生活缴费",
    "医疗",
    "社交",
    "住房",
    "收入",
    "其他",
]
PositiveAmount = Annotated[Decimal, Field(gt=0)]
Confidence = Annotated[Decimal, Field(ge=0, le=1)]

CATEGORIES = (
    "餐饮、交通、购物、娱乐、学习、生活缴费、医疗、社交、住房、收入、其他"
)

SYSTEM_PROMPT = f"""
你是个人记账文本解析器。请把用户输入转换为一个 JSON 对象，只返回 JSON，
不要返回 Markdown、解释或额外文字。

顶层只包含 transactions 数组，每笔独立收支对应一个元素，单笔也放在数组中。
例如“午饭23，晚饭30，笔记本33”必须拆成三笔，金额分别为23、30、33，不能合并。
同一笔消费的商家、支付方式等补充描述不要拆成额外账单。

每笔账单的字段和规则：
1. type 只能是 expense 或 income。默认 expense；工资、生活费到账、退款、
   报销到账等明确入账内容使用 income。
2. amount 必须来自用户原文，不得编造。无法识别时返回 null。金额必须大于 0。
   正确理解“18块5”“二十”“20r”“20元”“20块”等表达。
3. currency 默认 CNY。
4. category 只能是：{CATEGORIES}。无法判断时使用“其他”，不得创造新一级分类。
5. subcategory、merchant、payment_method、note 无法判断时返回 null。
6. 未提日期时 occurred_at 使用给定当前时间；昨天、前天、上周五等相对时间
   必须结合当前时间与时区转换为带 UTC 偏移的 ISO 8601 时间。
7. tags 返回字符串数组，没有标签时返回空数组。
8. confidence 返回 0 到 1 之间的数字。

必须严格返回以下结构，不要增加字段，transactions 至少包含一笔账单：
{{"transactions": [{{
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
  "confidence": 0.95
}}]}}
""".strip()


class CompletionClient(Protocol):
    """解析器所需的最小大模型客户端接口。"""

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """返回大模型生成的文本。"""
        ...


class ParsedTransaction(BaseModel):
    """经过校验的结构化账单解析结果。"""

    model_config = ConfigDict(extra="forbid")

    type: TransactionType
    amount: PositiveAmount | None
    currency: str = Field(default="CNY", pattern=r"^[A-Z]{3}$")
    category: Category
    subcategory: str | None
    merchant: str | None
    payment_method: str | None
    occurred_at: datetime
    note: str | None
    tags: list[str]
    confidence: Confidence

    @field_validator("occurred_at")
    @classmethod
    def occurred_at_must_include_timezone(
        cls,
        value: datetime,
    ) -> datetime:
        """确保账单发生时间包含明确的时区偏移。"""
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must include a timezone offset.")
        return value


class ParsedTransactions(BaseModel):
    """一次输入中解析出的多笔账单，不改变单笔账单结构。"""

    model_config = ConfigDict(extra="forbid")

    transactions: list[ParsedTransaction] = Field(min_length=1)


class TransactionParserError(Exception):
    """账单解析异常的基类。"""


class InvalidParserInputError(TransactionParserError):
    """账单解析输入无效。"""


class InvalidModelOutputError(TransactionParserError):
    """模型输出在清理和重试后仍无法通过校验。"""


def clean_json_text(raw_text: str) -> str:
    """去除模型可能附带的 Markdown JSON 代码块。"""
    cleaned = raw_text.strip()
    fenced_match = re.fullmatch(
        r"```(?:json)?\s*(.*?)\s*```",
        cleaned,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if fenced_match:
        return fenced_match.group(1).strip()
    return cleaned


def _safe_response_snippet(raw_text: str) -> str:
    """生成单行、定长的模型响应日志片段。"""
    normalized = " ".join(raw_text.split())
    if len(normalized) <= _MAX_LOGGED_RESPONSE_CHARS:
        return normalized
    return f"{normalized[:_MAX_LOGGED_RESPONSE_CHARS]}..."


def validate_model_output(raw_text: str) -> list[ParsedTransaction]:
    """清理、解析并校验模型输出。"""
    cleaned = clean_json_text(raw_text)
    data = json.loads(
        cleaned,
        parse_float=Decimal,
        parse_int=Decimal,
    )
    if not isinstance(data, dict):
        raise ValueError("The LLM output must be a JSON object.")
    # 兼容模型仍然返回旧版单笔对象的情况。
    if "transactions" not in data:
        return [ParsedTransaction.model_validate(data)]
    return ParsedTransactions.model_validate(data).transactions


class TransactionParser:
    """组织提示词、调用模型并校验结构化账单。"""

    def __init__(
        self,
        llm_client: CompletionClient,
        default_timezone: str | None = None,
    ) -> None:
        self.llm_client = llm_client
        self.default_timezone = (
            default_timezone or get_settings().default_timezone
        )

    def parse(
        self,
        text: str,
        current_time: datetime | None = None,
        timezone_name: str | None = None,
    ) -> list[ParsedTransaction]:
        """解析自然语言中的一笔或多笔账单，非法模型输出最多重试一次。"""
        normalized_text = text.strip()
        if not normalized_text:
            raise InvalidParserInputError("Transaction text cannot be empty.")
        resolved_timezone = timezone_name or self.default_timezone

        localized_time = self._resolve_current_time(
            current_time,
            resolved_timezone,
        )
        user_prompt = self._build_user_prompt(
            normalized_text,
            localized_time,
            resolved_timezone,
        )
        last_error: Exception | None = None

        for attempt in range(2):
            retry_prompt = user_prompt
            if attempt == 1:
                retry_prompt += (
                    "\n上一次输出不是符合要求的 JSON。请重新解析，并且只返回"
                    "完整、合法、字段严格匹配的 JSON 对象。"
                )
            raw_output = self.llm_client.complete(
                SYSTEM_PROMPT,
                retry_prompt,
            )
            try:
                return validate_model_output(raw_output)
            except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                logger.warning(
                    "LLM parser output invalid attempt=%s will_retry=%s "
                    "error_type=%s response_snippet=%r",
                    attempt + 1,
                    attempt == 0,
                    type(exc).__name__,
                    _safe_response_snippet(raw_output),
                )
                last_error = exc

        raise InvalidModelOutputError(
            "The LLM output is invalid after one retry."
        ) from last_error

    @staticmethod
    def _resolve_current_time(
        current_time: datetime | None,
        timezone_name: str,
    ) -> datetime:
        """校验时区，并把当前时间转换到指定时区。"""
        try:
            target_timezone = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise InvalidParserInputError(
                "The timezone name is invalid."
            ) from exc

        if current_time is None:
            return datetime.now(target_timezone)
        if current_time.tzinfo is None:
            return current_time.replace(tzinfo=target_timezone)
        return current_time.astimezone(target_timezone)

    @staticmethod
    def _build_user_prompt(
        text: str,
        current_time: datetime,
        timezone_name: str,
    ) -> str:
        """把用户文本和时间上下文组合成模型输入。"""
        return (
            f"用户原文：{text}\n"
            f"当前时间：{current_time.isoformat()}\n"
            f"时区：{timezone_name}"
        )
