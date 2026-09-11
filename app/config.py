"""从环境变量读取应用配置。"""

from functools import lru_cache
import os

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict


DEFAULT_LLM_BASE_URL = "https://api.deepseek.com"
DEFAULT_LLM_MODEL = "deepseek-flash"


class Settings(BaseModel):
    """记账服务的运行时配置。"""

    model_config = ConfigDict(frozen=True)

    llm_api_key: str = ""
    llm_base_url: str = DEFAULT_LLM_BASE_URL
    llm_model: str = DEFAULT_LLM_MODEL
    llm_timeout_seconds: float = 8.0
    app_api_token: str = ""
    admin_api_token: str = ""
    default_timezone: str = "Asia/Taipei"
    database_url: str = "sqlite:///./data/bookkeeping.db"

    @classmethod
    def from_environment(cls) -> "Settings":
        """从本地 .env 文件和进程环境变量构建配置。"""
        load_dotenv()
        return cls(
            llm_api_key=os.getenv("LLM_API_KEY", ""),
            llm_base_url=os.getenv("LLM_BASE_URL", DEFAULT_LLM_BASE_URL),
            llm_model=os.getenv("LLM_MODEL", DEFAULT_LLM_MODEL),
            llm_timeout_seconds=float(
                os.getenv("LLM_TIMEOUT_SECONDS", "8")
            ),
            app_api_token=os.getenv("APP_API_TOKEN", ""),
            admin_api_token=os.getenv("ADMIN_API_TOKEN", ""),
            default_timezone=os.getenv("DEFAULT_TIMEZONE", "Asia/Taipei"),
            database_url=os.getenv(
                "DATABASE_URL", "sqlite:///./data/bookkeeping.db"
            ),
        )


@lru_cache
def get_settings() -> Settings:
    """返回进程内共享的只读应用配置。"""
    return Settings.from_environment()
