from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "dev"
    log_level: str = "INFO"
    database_url: str = "sqlite:///data/powertrade.db"
    request_timeout_seconds: int = 20
    request_retry_times: int = 2
    user_agent: str = "powertrade-crawler/0.1"
    gridstatus_min_interval_seconds: float = 2.1
    entsoe_min_interval_seconds: float = 0.25
    elexon_min_interval_seconds: float = 0.25
    elecheck_area_code: str = "320000000000"
    elecheck_start_date: str | None = None
    elecheck_end_date: str | None = None

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
