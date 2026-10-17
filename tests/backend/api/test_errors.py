from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from backend.api import errors


def _client(exc: Exception) -> TestClient:
    app = FastAPI()
    errors.install(app)

    @app.get("/boom")
    def boom():
        raise exc

    @app.get("/bug")
    def bug():
        raise KeyError("a real bug")

    return TestClient(app, raise_server_exceptions=False)


def test_connection_reset_is_a_503():
    # What asyncpg raises when Neon drops the TLS handshake.
    response = _client(ConnectionResetError(54, "Connection reset by peer")).get("/boom")
    assert response.status_code == 503
    assert response.json() == {"detail": "Database unavailable"}


def test_timeout_and_wrapped_operational_errors_are_503s():
    assert _client(TimeoutError()).get("/boom").status_code == 503
    wrapped = OperationalError("select 1", {}, Exception("server closed the connection"))
    assert _client(wrapped).get("/boom").status_code == 503


def test_other_errors_stay_500s():
    # A real bug must not be disguised as "database unavailable".
    assert _client(ConnectionResetError()).get("/bug").status_code == 500
