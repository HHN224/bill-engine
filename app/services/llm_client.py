"""兼容 OpenAI Chat Completions 风格的大模型客户端。"""

from collections.abc import Mapping
from typing import Any

import httpx

from app.config import DEFAULT_LLM_BASE_URL, Settings, get_settings
from app.logging_config import get_application_logger


_MAX_LOGGED_RESPONSE_CHARS = 500
logger = get_application_logger(__name__)


def _safe_response_snippet(raw_text: str) -> str:
    """生成单行、定长的模型响应日志片段。"""
    normalized = " ".join(raw_text.split())
    if len(normalized) <= _MAX_LOGGED_RESPONSE_CHARS:
        return normalized
    return f"{normalized[:_MAX_LOGGED_RESPONSE_CHARS]}..."


class LLMError(Exception):
    """所有大模型客户端异常的基类。"""


class LLMConfigurationError(LLMError):
    """大模型配置缺失或无效。"""


class LLMTimeoutError(LLMError):
    """大模型请求超时。"""


class LLMServiceError(LLMError):
    """大模型服务请求失败。"""


class LLMResponseError(LLMError):
    """大模型响应结构无效。"""


class LLMClient:
    """通过 Chat Completions 接口获取文本结果。"""

    def __init__(
        self,
        settings: Settings | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._http_client = http_client

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """发送一次对话请求并返回助手文本。"""
        self._validate_configuration()
        request_url = self._chat_completions_url()
        headers = {
            "Authorization": f"Bearer {self.settings.llm_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.settings.llm_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }

        try:
            if self._http_client is not None:
                response = self._http_client.post(
                    request_url,
                    headers=headers,
                    json=payload,
                    timeout=self.settings.llm_timeout_seconds,
                )
            else:
                with httpx.Client() as client:
                    response = client.post(
                        request_url,
                        headers=headers,
                        json=payload,
                        timeout=self.settings.llm_timeout_seconds,
                    )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            logger.error(
                "LLM request timed out timeout_seconds=%s",
                self.settings.llm_timeout_seconds,
            )
            raise LLMTimeoutError("The LLM request timed out.") from exc
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            status_code: int | str = (
                exc.response.status_code
                if isinstance(exc, httpx.HTTPStatusError)
                else "unavailable"
            )
            logger.error(
                "LLM service request failed status_code=%s",
                status_code,
            )
            raise LLMServiceError("The LLM request failed.") from exc

        return self._extract_content(response)

    def _validate_configuration(self) -> None:
        """在发送请求前检查必需配置。"""
        if not self.settings.llm_api_key.strip():
            raise LLMConfigurationError("LLM_API_KEY is not configured.")
        if not self.settings.llm_model.strip():
            raise LLMConfigurationError("LLM_MODEL is not configured.")
        if self.settings.llm_timeout_seconds <= 0:
            raise LLMConfigurationError(
                "LLM_TIMEOUT_SECONDS must be greater than zero."
            )

    def _chat_completions_url(self) -> str:
        """生成 Chat Completions 请求地址。"""
        base_url = (
            self.settings.llm_base_url.strip() or DEFAULT_LLM_BASE_URL
        ).rstrip("/")
        if base_url.endswith("/chat/completions"):
            return base_url
        return f"{base_url}/chat/completions"

    @staticmethod
    def _extract_content(response: httpx.Response) -> str:
        """从兼容响应中提取第一条助手文本。"""
        try:
            response_data: dict[str, Any] = response.json()
            choices = response_data["choices"]
            content = choices[0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            logger.error(
                "LLM response invalid status_code=%s error_type=%s "
                "response_snippet=%r",
                response.status_code,
                type(exc).__name__,
                _safe_response_snippet(response.text),
            )
            raise LLMResponseError(
                "The LLM response has an invalid structure."
            ) from exc

        if isinstance(content, str) and content.strip():
            return content
        if isinstance(content, list):
            text_parts = [
                item.get("text", "")
                for item in content
                if isinstance(item, Mapping)
                and item.get("type") in {"text", "output_text"}
            ]
            joined_text = "".join(text_parts)
            if joined_text.strip():
                return joined_text

        raise LLMResponseError("The LLM response contains no text.")
