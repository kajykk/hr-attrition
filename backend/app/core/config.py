"""应用配置 - 基于 pydantic-settings 从环境变量加载（参考 D10 2.2）."""
from functools import lru_cache

from pydantic import field_validator, model_validator
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

    # ===== 登录防爆破（连续失败锁定） =====
    LOGIN_MAX_FAILED_ATTEMPTS: int = 5
    LOGIN_LOCKOUT_MINUTES: int = 15

    # ===== PII Fernet 加密（ADR-007） =====
    PII_FERNET_KEY: str = "change-me-generate-fernet-key"
    PII_KEY_ROTATION_DAYS: int = 90

    # PII 检索哈希 pepper（HMAC-SHA256）。缺失时从 PII_FERNET_KEY 派生并告警。
    # 注意：更换 pepper 会使存量 *_hash 索引失效，需离线重算迁移。
    PII_HASH_PEPPER: str = ""

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

    # ===== RAG 知识库问答（feat/rag-kb，仅 PostgreSQL + 安装 .[rag] 后启用） =====
    RAG_ENABLED: bool = False  # 默认关闭；compose/.env 中显式开启
    RAG_EMBEDDING_MODEL: str = "text-embedding-v3"
    RAG_EMBEDDING_DIM: int = 1024
    RAG_EMBEDDING_PROVIDER: str = "dashscope"  # dashscope | hash（hash 仅限离线开发/测试）
    RAG_RERANK_ENABLED: bool = True
    RAG_RERANK_MODEL: str = "gte-rerank"
    RAG_RERANK_TIMEOUT_MS: int = 800
    RAG_MIN_SCORE: float = 0.012  # RRF 尺度：单路第 1 名≈1/61≈0.016；低于 0.012 视为无可信命中 → 拒答
    RAG_MIN_COSINE: float = 0.62  # 拒答双信号①：查询-最优块语义相似度下限（按 text-embedding-v3 标定：可答组 min 0.638 / 不可答组 max 0.613）
    RAG_MIN_COVERAGE: float = 0.26  # 拒答双信号②：查询词元被最优块覆盖的比例下限
    RAG_CHUNK_TOKENS: int = 512
    RAG_CHUNK_OVERLAP: int = 64
    RAG_TOP_K: int = 5  # 最终送入 Prompt 的资料块数
    RAG_RECALL_CANDIDATES: int = 12  # 融合前每路召回候选数

    # ===== 限流 =====
    RATE_LIMIT_LOGIN: str = "5/minute"
    RATE_LIMIT_API: str = "100/minute"
    # 反向代理可信层数（nginx 等）：XFF 从右往左取第 N 段作为真实客户端 IP。
    # 仅当 8000 端口不直接对外暴露时该值才有效；直连场景应保持为 0/1 并绑定内网。
    TRUSTED_PROXY_COUNT: int = 1

    # ===== 业务 =====
    PASSWORD_RESET_BASE_URL: str = "http://localhost:5173"

    @field_validator("CORS_ORIGINS")
    @classmethod
    def _validate_cors(cls, v: str) -> str:
        return v or "http://localhost:5173"

    @model_validator(mode="after")
    def _reject_placeholder_secrets_in_prod(self):
        """生产环境拒绝默认占位密钥（P0-3）.

        若 APP_ENV=production 但 JWT_SECRET / PII_FERNET_KEY 仍为默认占位值，
        直接抛错拒绝启动，避免用弱密钥上线。
        """
        if self.APP_ENV == "production":
            defaults = [
                ("JWT_SECRET", "change-me-64-char-random-secret-please-generate-securely"),
                ("PII_FERNET_KEY", "change-me-generate-fernet-key"),
            ]
            for field_name, default in defaults:
                value = getattr(self, field_name)
                if value == default or not value:
                    raise ValueError(
                        f"生产环境必须为 {field_name} 配置强随机密钥（当前为默认占位值）"
                    )
        return self

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def is_prod(self) -> bool:
        return self.APP_ENV == "production"


@lru_cache
def get_settings() -> Settings:
    """单例配置."""
    return Settings()


settings = get_settings()
