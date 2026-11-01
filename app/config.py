from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_secret: str = "dev-secret-change-me"
    app_host: str = "0.0.0.0"
    app_port: int = 8000

    admin_username: str = "admin"
    admin_password: str = "admin"

    telegram_bot_token: str = ""
    telegram_webhook_url: str = ""
    mini_app_url: str = ""

    gemini_api_key: str = ""
    openrouter_api_key: str = ""

    database_url: str = f"sqlite+aiosqlite:///{BASE_DIR / 'data' / 'fintrack.db'}"

    @property
    def data_dir(self) -> Path:
        d = BASE_DIR / "data"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def reports_dir(self) -> Path:
        d = BASE_DIR / "reports"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def backups_dir(self) -> Path:
        d = BASE_DIR / "backups"
        d.mkdir(parents=True, exist_ok=True)
        return d


@lru_cache
def get_settings() -> Settings:
    return Settings()
