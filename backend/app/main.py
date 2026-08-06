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
from app.core.rate_limit import init_limiter
from app.core.redis import close_redis, init_redis
from app.core.request_id import RequestIdMiddleware
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
    # 非生产环境建表兜底（生产环境必须使用 alembic upgrade head）
    if not settings.is_prod:
        try:
            from app.db.base import Base
            from app.db.session import engine

            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            logger.info("开发环境建表完成（create_all，生产请用 alembic）")
        except Exception as e:  # noqa: BLE001
            logger.warning("开发环境 create_all 失败（请确认 DB 可用） | err=%s", e)
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

# 请求 ID 中间件（P2-11：全链路追踪）
app.add_middleware(RequestIdMiddleware)

# 限流（slowapi，P1-6 登录防爆破）
init_limiter(app)

# ===== 业务路由（D05 v1） =====
app.include_router(api_router, prefix="/api/v1")


# ===== 健康检查（D05 3.9 GET /admin/health） =====
@app.get("/health", tags=["health"])
async def health() -> dict:
    """健康检查端点（公开）.

    P2-11：真实探测依赖（DB SELECT 1 / Redis PING），探测失败标记 degraded
    但不返回 5xx（避免 LB 误判）；LLM 未配置时标记 not_configured。
    """
    components: dict = {}

    # 数据库探测（短超时，失败不抛）
    try:
        from sqlalchemy import text

        from app.db.session import engine

        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        components["database"] = "healthy"
    except Exception as e:  # noqa: BLE001
        logger.warning("健康检查：数据库探测失败 | err=%s", e)
        components["database"] = "degraded"

    # Redis 探测
    redis_ok = False
    try:
        from app.core.redis import get_redis

        redis = get_redis()
        if redis is None:
            components["redis"] = "not_configured"
        else:
            await redis.ping()
            components["redis"] = "healthy"
            redis_ok = True
    except Exception as e:  # noqa: BLE001
        logger.warning("健康检查：Redis 探测失败 | err=%s", e)
        components["redis"] = "degraded"

    # Celery 探测（P2-11：从 Redis 心跳 key 判断 worker/beat 存活度）
    if not redis_ok:
        components["celery"] = "not_configured"
    else:
        try:
            from app.core.celery_heartbeat import is_heartbeat_fresh
            from app.core.kill_switch import _get_sync_redis

            fresh = is_heartbeat_fresh(_get_sync_redis())
            components["celery"] = "healthy" if fresh else "degraded"
        except Exception as e:  # noqa: BLE001
            logger.warning("健康检查：Celery 心跳探测失败 | err=%s", e)
            components["celery"] = "degraded"

    components["llm"] = "healthy" if settings.DASHSCOPE_API_KEY else "not_configured"

    status = "healthy" if all(v == "healthy" for v in components.values() if v != "not_configured") else "degraded"
    return {
        "status": status,
        "version": "1.0.0",
        "env": settings.APP_ENV,
        "components": components,
    }


@app.get("/", tags=["root"])
async def root() -> dict:
    """根路径."""
    return {"app": "HRA", "version": "1.0.0", "docs": "/docs"}
