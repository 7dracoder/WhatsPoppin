from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)


def test_frontend_dev_server_is_allowed():
    resp = client.get("/", headers={"Origin": "http://localhost:5173"})
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_preflight_from_frontend_is_allowed():
    resp = client.options("/", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"})
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_other_origins_are_not_allowed():
    resp = client.get("/", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in resp.headers
