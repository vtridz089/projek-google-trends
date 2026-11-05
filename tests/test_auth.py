"""Tes login: token sesi, pembatas percobaan, dan alur lewat API (alur API perlu FastAPI)."""
import pytest

from app import auth


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("TRENDWATCH_PASSWORD", "rahasia")
    monkeypatch.delenv("TRENDWATCH_USER", raising=False)
    monkeypatch.delenv("TRENDWATCH_SECRET", raising=False)
    monkeypatch.delenv("TRENDWATCH_SESSION_HOURS", raising=False)


def test_enabled_only_with_password(monkeypatch):
    assert auth.enabled()
    monkeypatch.delenv("TRENDWATCH_PASSWORD")
    assert not auth.enabled() and not auth.check_credentials("admin", "")


def test_check_credentials():
    assert auth.check_credentials("admin", "rahasia")
    assert not auth.check_credentials("admin", "salah") and not auth.check_credentials("budi", "rahasia")
    assert not auth.check_credentials("", "")


def test_token_roundtrip_expiry_and_tamper():
    t = auth.make_token("admin", now=1000)
    assert auth.verify_token(t, now=1000 + 3600) == "admin"
    assert auth.verify_token(t, now=1000 + 13 * 3600) is None            # lewat 12 jam
    user, exp, sig = t.split("|")
    assert auth.verify_token(f"admin|{int(exp) + 99999}|{sig}", now=1000) is None   # umur diperpanjang sendiri
    assert auth.verify_token(f"{user}|{exp}|{'0' * len(sig)}", now=1000) is None
    for bad in (None, "", "abc", "a|b", "a|b|c|d", "admin|x|" + sig):
        assert auth.verify_token(bad, now=1000) is None


def test_changing_password_or_secret_invalidates_sessions(monkeypatch):
    t = auth.make_token("admin", now=1000)
    monkeypatch.setenv("TRENDWATCH_PASSWORD", "baru")
    assert auth.verify_token(t, now=1001) is None
    monkeypatch.setenv("TRENDWATCH_PASSWORD", "rahasia")
    assert auth.verify_token(t, now=1001) == "admin"
    monkeypatch.setenv("TRENDWATCH_SECRET", "kunci-lain")
    assert auth.verify_token(t, now=1001) is None


def test_token_for_other_user_rejected(monkeypatch):
    t = auth.make_token("admin", now=1000)
    monkeypatch.setenv("TRENDWATCH_USER", "budi")
    assert auth.verify_token(t, now=1001) is None


def test_session_hours(monkeypatch):
    monkeypatch.setenv("TRENDWATCH_SESSION_HOURS", "1")
    assert auth.session_seconds() == 3600
    monkeypatch.setenv("TRENDWATCH_SESSION_HOURS", "bukan angka")
    assert auth.session_seconds() == 12 * 3600


@pytest.mark.parametrize("given, expected", [
    ("/", "/"), ("/?seed=cpns", "/?seed=cpns"), (None, "/"), ("", "/"),
    ("//evil.com", "/"), ("https://evil.com", "/"), ("/\\evil.com", "/"), ("/login", "/"), ("/api/keywords", "/"),
    ("/x\r\nSet-Cookie: a=b", "/")])
def test_safe_next_blocks_open_redirect(given, expected):
    assert auth.safe_next(given) == expected


def test_throttle_blocks_after_limit_and_recovers():
    th = auth.Throttle(limit=3, window=100)
    for i in range(3):
        assert th.retry_after("ip", now=i) == 0
        th.fail("ip", now=i)
    assert th.retry_after("ip", now=3) == 98 and th.retry_after("lain", now=3) == 0
    assert th.retry_after("ip", now=102) == 0
    th.reset("ip")
    assert th.retry_after("ip", now=3) == 0


# --------------------------------------------------------------------------- alur lewat API (perlu FastAPI)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    monkeypatch.setenv("TRENDWATCH_DB", str(tmp_path / "a.db"))
    monkeypatch.setenv("TRENDWATCH_CSV", str(tmp_path / "s.csv"))
    from fastapi.testclient import TestClient

    from app import main
    main._throttle = auth.Throttle()
    with TestClient(main.app, follow_redirects=False) as c:
        yield c


def test_login_flow(client):
    assert client.get("/api/health").status_code == 200
    r = client.get("/?seed=cpns")
    assert r.status_code == 303 and r.headers["location"].startswith("/login?next=")
    assert client.get("/login").status_code == 200
    assert client.get("/api/keywords?seed=a&demo=true").status_code == 401

    bad = client.post("/api/login", json={"username": "admin", "password": "salah"})
    assert bad.status_code == 401 and "tw_session" not in bad.cookies

    ok = client.post("/api/login", json={"username": "admin", "password": "rahasia", "next": "//evil.com"})
    assert ok.status_code == 200 and ok.json()["next"] == "/"
    assert "httponly" in ok.headers["set-cookie"].lower() and "samesite=lax" in ok.headers["set-cookie"].lower()
    assert client.get("/").status_code == 200
    assert client.get("/api/keywords?seed=cpns&demo=true").status_code == 200
    assert client.get("/login").status_code == 303                       # sudah masuk
    assert client.get("/api/session").json() == {"auth": True, "user": "admin"}

    client.post("/api/logout")
    assert client.get("/api/keywords?seed=cpns&demo=true").status_code == 401


def test_login_is_throttled_and_basic_still_works(client):
    for _ in range(5):
        assert client.post("/api/login", json={"username": "admin", "password": "x"}).status_code == 401
    r = client.post("/api/login", json={"username": "admin", "password": "rahasia"})
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0
    assert client.get("/api/session", auth=("admin", "rahasia")).status_code == 200


def test_no_login_when_password_unset(client, monkeypatch):
    monkeypatch.delenv("TRENDWATCH_PASSWORD")
    assert client.get("/").status_code == 200 and client.get("/login").status_code == 303
    assert client.post("/api/login", json={}).status_code == 404
    assert client.get("/api/session").json() == {"auth": False, "user": None}
