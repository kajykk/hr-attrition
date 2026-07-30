"""FastAPI 应用入口 - HRA 后端（D03 2.2 API Gateway 容器）.

挂载：
  - CORS 中间件（D03 2.3）
  - 租户隔离中间件（ADR-002，从 JWT 注入 tenant_id）
  - /health 健康检查
  - /api/v1/* 业务路由（auth/employees/risk/warnings/advise）
  - /openapi.json OpenAPI Spec
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import api_router
from app.core.config import settings
from app.core.logging import get_logger, setup_logging
from app.core.redis import close_redis, init_redis
from app.core.tenant import tenant_middleware

setup_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动/关闭钩子（含 Redis 生命周期管理）."""
    logger.info("HRA 后端启动 | env=%s", settings.APP_ENV)
    # 初始化 Redis（失败不崩溃，降级无缓存模式）
    try:
        await init_redis()
    except Exception as e:  # noqa: BLE001
        logger.warning("Redis 初始化异常（不影响启动） | err=%s", e)
    yield
    # 关闭 Redis
    try:
        await close_redis()
    except Exception as e:  # noqa: BLE001
        logger.warning("Redis 关闭异常 | err=%s", e)
    logger.info("HRA 后端关闭")


app = FastAPI(
    title="HRA - 企业员工离职风险与人才流失预警系统",
    description="FastAPI + SQLAlchemy async + Celery + 通义千问 Max",
    version="1.0.0",
    lifespan=lifespan,
    openapi_url="/openapi.json",
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS（D03 2.3）
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 租户隔离中间件（ADR-002，从 JWT 注入 tenant_id 到 ContextVar）
app.middleware("http")(tenant_middleware)

# ===== 业务路由（D05 v1） =====
app.include_router(api_router, prefix="/api/v1")


# ===== 健康检查（D05 3.9 GET /admin/health） =====
@app.get("/health", tags=["health"])
async def health() -> dict:
    """健康检查端点（公开）."""
    return {
        "status": "healthy",
        "version": "1.0.0",
        "env": settings.APP_ENV,
        "components": {
            "database": "healthy",
            "redis": "healthy",
            "celery": "healthy",
            "llm": "healthy" if settings.DASHSCOPE_API_KEY else "not_configured",
        },
    }


@app.get("/", tags=["root"])
async def root() -> dict:
    """根路径."""
    return {"app": "HRA", "version": "1.0.0", "docs": "/docs"}
