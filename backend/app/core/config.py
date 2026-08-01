"""应用配置 - 基于 pydantic-settings 从环境变量加载（参考 D10 2.2）."""
from functools import lru_cache
from typing import List

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置项.

    所有配置均从环境变量读取，参考 D10 2.2 配置项清单。
    LLM 默认通义千问 Max（qwen-max），OpenAI 路径默认禁用（ADR-003）。
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ===== 应用 =====
    APP_ENV: str = "development"
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "text"  # text | json（生产建议 json，便于采集）
    CORS_ORIGINS: str = "http://localhost:5173,http://localhost:3000"

    # ===== 数据库 =====
    DATABASE_URL: str = "sqlite+aiosqlite:///./hra_dev.db"

    # ===== Redis =====
    REDIS_URL: str = "redis://localhost:6379/0"

    # ===== JWT =====
    JWT_SECRET: str = "change-me-64-char-random-secret-please-generate-securely"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_EXPIRE_MINUTES: int = 30
    JWT_REFRESH_EXPIRE_DAYS: int = 7

    # ===== PII Fernet 加密（ADR-007） =====
    PII_FERNET_KEY: str = "change-me-generate-fernet-key"
    PII_KEY_ROTATION_DAYS: int = 90

    # ===== LLM（主：通义千问 Max，规避跨境传输） =====
    DASHSCOPE_API_KEY: str = ""
    LLM_PRIMARY: str = "qwen-max"
    LLM_FALLBACK: str = "deepseek-v3"
    LLM_OPTIONAL: str = "gpt-4-turbo"
    OPENAI_API_KEY: str = ""
    OPENAI_ENABLED: bool = False  # OpenAI 路径默认禁用，需数据出境评估通过
    LLM_TIMEOUT: int = 30
    LLM_MAX_RETRIES: int = 3
    LLM_RATE_LIMIT: int = 10

    # ===== 限流 =====
    RATE_LIMIT_LOGIN: str = "5/minute"
    RATE_LIMIT_API: str = "100/minute"

    # ===== 业务 =====
    PASSWORD_RESET_BASE_URL: str = "http://localhost:5173"

    @field_validator("CORS_ORIGINS")
    @classmethod
    def _validate_cors(cls, v: str) -> str:
        return v or "http://localhost:5173"

    @property
    def cors_origins_list(self) -> List[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def is_prod(self) -> bool:
        return self.APP_ENV == "production"


@lru_cache
def get_settings() -> Settings:
    """单例配置."""
    return Settings()


settings = get_settings()
