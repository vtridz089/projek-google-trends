import pytest


@pytest.fixture(autouse=True)
def _auth_off_by_default(monkeypatch):
    """Login wajib di produksi; tes lain tidak membahasnya. tests/test_auth.py menyalakannya sendiri."""
    monkeypatch.setenv("TRENDWATCH_AUTH", "off")
