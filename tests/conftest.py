import pytest

# backend.main calls load_dotenv(), so a developer's .env (with a real NICRRON_API_KEY) can leak
# into any test that imports it. Nicrron becomes the default provider whenever that key is set,
# so clear it, and the provider/model overrides, for every test. Tests that exercise Nicrron set
# these themselves.
_GATEWAY_SETTINGS = ("NICRRON_API_KEY", "NICRRON_MODEL", "NICRRON_JEV_MODEL", "LLM_PROVIDER", "LLM_MODEL")


@pytest.fixture(autouse=True)
def no_real_gateway(monkeypatch):
    for name in _GATEWAY_SETTINGS:
        monkeypatch.delenv(name, raising=False)
