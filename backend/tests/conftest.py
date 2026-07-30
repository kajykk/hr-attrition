"""pytest 配置 - FastAPI TestClient + 占位 PII Fernet key."""
import os
import sys
from pathlib import Path

# 注入 backend 目录到 sys.path（便于 import app）
backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))

# 测试环境变量（避免真实 Fernet key 缺失）
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://hra:hra@localhost:5432/hra_test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/1")
os.environ.setdefault("JWT_SECRET", "test-secret-64-char-random-please-generate-securely-test")
os.environ.setdefault("LOG_LEVEL", "WARNING")

# 生成测试用 Fernet key（避免启动时 key 不合法）
from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("PII_FERNET_KEY", Fernet.generate_key().decode())

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture
def client() -> TestClient:
    """FastAPI TestClient."""
    return TestClient(app)
