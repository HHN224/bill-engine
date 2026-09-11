from app.config import Settings


def test_settings_defaults() -> None:
    settings = Settings()

    assert settings.default_timezone == "Asia/Taipei"
    assert settings.database_url == "sqlite:///./data/bookkeeping.db"
    assert settings.llm_base_url == "https://api.deepseek.com"
    assert settings.llm_model == "deepseek-flash"
    assert settings.llm_timeout_seconds == 8.0
    assert settings.app_api_token == ""
    assert settings.admin_api_token == ""
