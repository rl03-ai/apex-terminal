from functools import lru_cache
from typing import List

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    app_name: str = 'Apex Signal API'
    debug: bool = True
    secret_key: str = 'change-me'
    access_token_expire_minutes: int = 60
    database_url: str = 'sqlite:///./apex.db'
    allowed_origins: List[str] = ['http://localhost:3000', 'http://localhost:5173']
    data_provider: str = 'demo'
    default_exchange: str = 'NASDAQ'
    demo_universe: List[str] = ['SOFI', 'RKLB', 'NOW', 'EOSE']
    yfinance_history_days: int = 370

    # Bulk ingestion
    ingest_workers: int = 8
    ingest_inter_delay: float = 0.25   # seconds between ticker submissions

    # Scheduler timezone
    scheduler_timezone: str = 'UTC'

    @field_validator('allowed_origins', mode='before')
    @classmethod
    def split_origins(cls, value: str | List[str]) -> List[str]:
        if isinstance(value, str):
            return [item.strip() for item in value.split(',') if item.strip()]
        return value

    @field_validator('demo_universe', mode='before')
    @classmethod
    def split_universe(cls, value: str | List[str]) -> List[str]:
        if isinstance(value, str):
            return [item.strip().upper() for item in value.split(',') if item.strip()]
        return [item.upper() for item in value]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
