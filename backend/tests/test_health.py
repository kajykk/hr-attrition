"""健康检查测试（GET /health 200）.

P2-11 起健康检查真实探测依赖：无 DB/Redis 环境应返回 degraded 而非硬编码 healthy。
"""
from datetime import UTC


def test_health_returns_200(client):
    """GET /health 应返回 200，status 为 healthy 或 degraded（真实探测）."""
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in ("healthy", "degraded")
    assert "components" in body
    assert "database" in body["components"]
    assert "redis" in body["components"]
    assert "celery" in body["components"]
    assert "llm" in body["components"]
    assert "version" in body


def test_health_returns_request_id_header(client):
    """健康检查响应应携带 X-Request-ID（P2-11 全链路追踪）."""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.headers.get("x-request-id")


def test_root_returns_app_info(client):
    """根路径返回应用信息."""
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["app"] == "HRA"


def test_openapi_available(client):
    """OpenAPI Spec 可访问（D05 1.2 /api/v1/openapi.json 在根路径暴露）."""
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    spec = resp.json()
    assert spec["info"]["title"].startswith("HRA")


def test_celery_heartbeat_roundtrip():
    """Celery 心跳写读回环：心跳任务写 key 后 /health 判定新鲜（P2-11）."""
    from app.core.celery_heartbeat import (
        heartbeat_age_seconds,
        is_heartbeat_fresh,
        write_heartbeat,
    )

    class _FakeRedis:
        """最小同步 Redis 模拟（set/get）."""

        def __init__(self) -> None:
            self._store: dict = {}

        def set(self, key: str, value: str) -> None:
            self._store[key] = value

        def setex(self, key: str, ttl: int, value: str) -> None:
            self._store[key] = value

        def get(self, key: str) -> str | None:
            return self._store.get(key)

    fake = _FakeRedis()

    # Redis 可用但无心跳 → 不新鲜（degraded）
    assert is_heartbeat_fresh(fake) is False
    assert heartbeat_age_seconds(fake) is None

    # 写入心跳 → 新鲜
    write_heartbeat(fake)
    assert is_heartbeat_fresh(fake) is True
    assert heartbeat_age_seconds(fake) is not None and heartbeat_age_seconds(fake) >= 0

    # Redis 不可用 → None（health 层标记 not_configured）
    assert is_heartbeat_fresh(None) is None


def test_celery_heartbeat_stale_is_not_fresh():
    """心跳过旧（超过阈值）应判定 degraded."""
    import json
    from datetime import datetime, timedelta

    from app.core.celery_heartbeat import is_heartbeat_fresh

    class _FakeStale:
        def get(self, key: str) -> str:
            old = datetime.now(UTC) - timedelta(seconds=3600)
            return json.dumps({"ts": old.isoformat()})

    assert is_heartbeat_fresh(_FakeStale()) is False
