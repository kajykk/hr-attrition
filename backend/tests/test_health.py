"""健康检查测试（GET /health 200）."""


def test_health_returns_200(client):
    """GET /health 应返回 200 与 healthy 状态."""
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "healthy"
    assert "components" in body
    assert "version" in body


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
