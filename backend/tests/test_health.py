"""健康检查测试（GET /health 200）.

P2-11 起健康检查真实探测依赖：无 DB/Redis 环境应返回 degraded 而非硬编码 healthy。
"""
import pytest


def test_health_returns_200(client):
    """GET /health 应返回 200，status 为 healthy 或 degraded（真实探测）."""
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in ("healthy", "degraded")
    assert "components" in body
    assert "database" in body["components"]
    assert "redis" in body["components"]
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
