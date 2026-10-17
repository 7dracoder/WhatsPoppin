"""backend/main.py mounts the graph router."""


def test_backend_main_mounts_the_router():
    from backend.main import app

    paths = set(app.openapi()["paths"])
    assert {"/graph/{ticker}", "/graph/{ticker}/board", "/companies/search"} <= paths
