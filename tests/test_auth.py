"""Tes akun dan login: hash, sesi, pembatas, daftar/masuk, ubah dan reset kata sandi, serta alur lewat API."""
import time

import pytest

from app import auth, db, mailer, users


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("TRENDWATCH_AUTH", "on")
    monkeypatch.setenv("TRENDWATCH_DB", str(tmp_path / "a.db"))
    for k in ("TRENDWATCH_SECRET", "TRENDWATCH_SESSION_HOURS", "TRENDWATCH_SMTP_HOST", "TRENDWATCH_BASE_URL",
              "TRENDWATCH_ADMIN_EMAIL", "TRENDWATCH_ADMIN_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    db.init_db()
    users.init()
    auth.reset_throttles()


def make(email="budi@contoh.id", pwd="rahasia123", name="Budi"):
    return auth.register(name, email, pwd, "1.1.1.1")


# --------------------------------------------------------------------------- kata sandi


def test_hash_is_salted_and_verifiable():
    a, b = auth.hash_password("rahasia123"), auth.hash_password("rahasia123")
    assert a != b and a.startswith("scrypt$") and "rahasia" not in a
    assert auth.verify_password("rahasia123", a) and not auth.verify_password("Rahasia123", a)
    for bad in ("", "x", "md5$1$2", "scrypt$14$8$1$zz$zz", None):
        assert not auth.verify_password("rahasia123", bad)


def test_enabled_only_off_by_explicit_switch(monkeypatch):
    assert auth.enabled()
    for off in ("off", "0", "FALSE", "no"):
        monkeypatch.setenv("TRENDWATCH_AUTH", off)
        assert not auth.enabled()
    monkeypatch.delenv("TRENDWATCH_AUTH")
    assert auth.enabled()                                  # bawaan: login wajib


@pytest.mark.parametrize("email", ["", "bukan-email", "a@b", "a b@c.id", "@c.id", "a@@c.id", "x" * 300 + "@c.id"])
def test_register_rejects_bad_email(email):
    with pytest.raises(auth.AuthError) as e:
        make(email=email)
    assert e.value.status == 400


def test_register_validation_and_duplicates():
    u = make(email="  Budi@Contoh.ID ")
    assert u["email"] == "budi@contoh.id" and u["name"] == "Budi" and u["password_hash"].startswith("scrypt$")
    with pytest.raises(auth.AuthError) as e:
        make(email="BUDI@contoh.id")
    assert e.value.status == 409
    for kw in ({"pwd": "pendek"}, {"pwd": "x" * 201}, {"name": "   "}, {"name": "n" * 81}):
        with pytest.raises(auth.AuthError) as e:
            make(email="lain@contoh.id", **kw)
        assert e.value.status == 400
    assert users.count() == 1


def test_register_is_throttled_per_ip():
    for i in range(10):
        auth.register("N", f"u{i}@c.id", "rahasia123", "9.9.9.9")
    with pytest.raises(auth.AuthError) as e:
        auth.register("N", "u11@c.id", "rahasia123", "9.9.9.9")
    assert e.value.status == 429 and e.value.retry_after > 0
    auth.register("N", "u12@c.id", "rahasia123", "8.8.8.8")        # IP lain tidak terpengaruh


# --------------------------------------------------------------------------- masuk


def test_login_ok_wrong_and_unknown_look_the_same():
    make()
    assert auth.login(" BUDI@contoh.id", "rahasia123", "2.2.2.2")["name"] == "Budi"
    msgs = set()
    for email, pwd in (("budi@contoh.id", "salah-salah"), ("tidak@ada.id", "rahasia123"), ("", "")):
        with pytest.raises(auth.AuthError) as e:
            auth.login(email, pwd, "2.2.2.2")
        assert e.value.status == 401
        msgs.add(e.value.message)
    assert len(msgs) == 1                                   # tidak membocorkan email mana yang ada


def test_login_locks_account_after_5_failures_even_from_many_ips_then_ok_resets():
    make()
    for i in range(5):
        with pytest.raises(auth.AuthError):
            auth.login("budi@contoh.id", "salah", f"10.0.0.{i}")
    with pytest.raises(auth.AuthError) as e:
        auth.login("budi@contoh.id", "rahasia123", "10.0.0.99")      # sandi benar pun ditolak saat terkunci
    assert e.value.status == 429
    auth.reset_throttles()
    for i in range(4):
        with pytest.raises(auth.AuthError):
            auth.login("budi@contoh.id", "salah", "3.3.3.3")
    auth.login("budi@contoh.id", "rahasia123", "3.3.3.3")
    for i in range(4):                                       # hitungan gagal dibersihkan setelah berhasil
        with pytest.raises(auth.AuthError) as e:
            auth.login("budi@contoh.id", "salah", "4.4.4.4")
        assert e.value.status == 401


# --------------------------------------------------------------------------- sesi


def test_token_roundtrip_expiry_tamper_and_unknown_user():
    u = make()
    t = auth.make_token(u, now=1000)
    assert auth.verify_token(t, now=1000 + 3600)["email"] == "budi@contoh.id"
    assert auth.verify_token(t, now=1000 + 8 * 24 * 3600) is None                    # lewat 7 hari
    uid, exp, fp, sig = t.split("|")
    assert auth.verify_token(f"{uid}|{int(exp) + 99999}|{fp}|{sig}", now=1000) is None
    assert auth.verify_token(f"2|{exp}|{fp}|{sig}", now=1000) is None                # ganti id pengguna
    assert auth.verify_token(f"{uid}|{exp}|{fp}|{'0' * len(sig)}", now=1000) is None
    for bad in (None, "", "abc", "1|2|3", "1|2|3|4|5", f"x|{exp}|{fp}|{sig}"):
        assert auth.verify_token(bad, now=1000) is None


def test_password_change_and_secret_change_invalidate_sessions(monkeypatch):
    u = make()
    t = auth.make_token(u, now=time.time())
    assert auth.verify_token(t)
    fresh = auth.change_password(u, "rahasia123", "sandibaru99")
    assert auth.verify_token(t) is None and auth.verify_token(auth.make_token(fresh))
    t2 = auth.make_token(fresh)
    monkeypatch.setenv("TRENDWATCH_SECRET", "kunci-lain")
    assert auth.verify_token(t2) is None


def test_session_key_is_generated_once_and_persisted():
    k1, k2 = auth._key(), auth._key()
    assert k1 == k2 and len(k1) == 64
    assert users.setting_get_or_create("session_key", lambda: "lain") == k1.decode()


def test_session_hours(monkeypatch):
    assert auth.session_seconds() == 168 * 3600
    monkeypatch.setenv("TRENDWATCH_SESSION_HOURS", "1")
    assert auth.session_seconds() == 3600
    monkeypatch.setenv("TRENDWATCH_SESSION_HOURS", "bukan angka")
    assert auth.session_seconds() == 168 * 3600


@pytest.mark.parametrize("given, expected", [
    ("/", "/"), ("/?seed=cpns", "/?seed=cpns"), (None, "/"), ("", "/"), ("//evil.com", "/"), ("https://evil.com", "/"),
    ("/\\evil.com", "/"), ("/login", "/"), ("/daftar", "/"), ("/reset?token=x", "/"), ("/api/keywords", "/"),
    ("/x\r\nSet-Cookie: a=b", "/"), ("/akun", "/akun")])
def test_safe_next_blocks_open_redirect(given, expected):
    assert auth.safe_next(given) == expected


def test_throttle_window():
    th = auth.Throttle(limit=3, window=100)
    for i in range(3):
        assert th.retry_after("ip", now=i) == 0
        th.fail("ip", now=i)
    assert th.retry_after("ip", now=3) == 98 and th.retry_after("lain", now=3) == 0
    assert th.retry_after("ip", now=102) == 0
    th.reset("ip")


# --------------------------------------------------------------------------- ubah kata sandi


def test_change_password_rules():
    u = make()
    with pytest.raises(auth.AuthError, match="saat ini salah"):
        auth.change_password(u, "salah", "sandibaru99")
    for new, msg in (("pendek", "minimal"), ("rahasia123", "berbeda")):
        with pytest.raises(auth.AuthError, match=msg):
            auth.change_password(u, "rahasia123", new)
    auth.change_password(u, "rahasia123", "sandibaru99")
    assert auth.login("budi@contoh.id", "sandibaru99", "5.5.5.5")
    with pytest.raises(auth.AuthError):
        auth.login("budi@contoh.id", "rahasia123", "5.5.5.5")


# --------------------------------------------------------------------------- lupa dan reset


def grab_link(caplog):
    line = next(r.getMessage() for r in caplog.records if "tautan reset" in r.getMessage())
    return line.split(": ", 1)[1].split("token=")[1].strip()


def test_forgot_logs_link_without_smtp_and_reset_is_single_use(caplog):
    make()
    caplog.set_level("WARNING", logger="trendwatch.auth")
    auth.forgot("budi@contoh.id", "6.6.6.6", "http://127.0.0.1:8000/")
    token = grab_link(caplog)
    assert len(token) >= 40
    with closing_conn() as c:                                # hanya hash token yang disimpan
        stored = [r[0] for r in c.execute("SELECT token_hash FROM reset_tokens")]
    assert stored and token not in stored[0]
    auth.reset(token, "sandibaru99")
    assert auth.login("budi@contoh.id", "sandibaru99", "7.7.7.7")
    with pytest.raises(auth.AuthError, match="tidak valid"):
        auth.reset(token, "laginya123")                      # sekali pakai


def closing_conn():
    from contextlib import closing
    return closing(db.connect())


def test_reset_token_expires_and_new_request_replaces_old(caplog):
    make()
    caplog.set_level("WARNING", logger="trendwatch.auth")
    t0 = time.time()
    auth.forgot("budi@contoh.id", "6.6.6.6", "http://x", now=t0)
    old = grab_link(caplog)
    caplog.clear()
    auth.forgot("budi@contoh.id", "6.6.6.6", "http://x", now=t0 + 10)
    new = grab_link(caplog)
    with pytest.raises(auth.AuthError):
        auth.reset(old, "sandibaru99", now=t0 + 20)          # token lama diganti
    with pytest.raises(auth.AuthError):
        auth.reset(new, "sandibaru99", now=t0 + 10 + auth.RESET_TTL + 1)   # kedaluwarsa
    caplog.clear()
    auth.forgot("budi@contoh.id", "6.6.6.6", "http://x", now=t0 + 5000)
    auth.reset(grab_link(caplog), "sandibaru99", now=t0 + 5100)


def test_forgot_unknown_or_invalid_email_is_silent_and_throttled(caplog):
    caplog.set_level("WARNING", logger="trendwatch.auth")
    auth.forgot("tidak@ada.id", "6.6.6.6", "http://x")
    auth.forgot("bukan email", "6.6.6.6", "http://x")
    assert not [r for r in caplog.records if "tautan reset" in r.getMessage()]
    for _ in range(3):
        auth.forgot("a@b.id", "6.6.6.6", "http://x")
    with pytest.raises(auth.AuthError) as e:
        auth.forgot("a@b.id", "6.6.6.6", "http://x")
    assert e.value.status == 429


def test_reset_rejects_weak_password_without_burning_token(caplog):
    make()
    caplog.set_level("WARNING", logger="trendwatch.auth")
    auth.forgot("budi@contoh.id", "6.6.6.6", "http://x")
    token = grab_link(caplog)
    with pytest.raises(auth.AuthError, match="minimal"):
        auth.reset(token, "pendek")
    auth.reset(token, "sandibaru99")


def test_reset_invalidates_existing_sessions(caplog):
    u = make()
    t = auth.make_token(u)
    caplog.set_level("WARNING", logger="trendwatch.auth")
    auth.forgot("budi@contoh.id", "6.6.6.6", "http://x")
    auth.reset(grab_link(caplog), "sandibaru99")
    assert auth.verify_token(t) is None


def test_forgot_sends_email_via_smtp_with_configured_base_url(monkeypatch):
    make()
    sent = []

    class FakeSMTP:
        def __init__(self, host, port, timeout=0): sent.append(("connect", host, port))
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self, context=None): sent.append("tls")
        def login(self, u, p): sent.append(("login", u, p))
        def send_message(self, msg): sent.append(("msg", msg["To"], msg["Subject"], msg.get_content()))

    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setenv("TRENDWATCH_SMTP_HOST", "smtp.contoh.id")
    monkeypatch.setenv("TRENDWATCH_SMTP_USER", "kirim@contoh.id")
    monkeypatch.setenv("TRENDWATCH_SMTP_PASSWORD", "pw")
    monkeypatch.setenv("TRENDWATCH_BASE_URL", "https://app.contoh.id")
    import threading
    auth.forgot("budi@contoh.id", "6.6.6.6", "http://penyerang.example/")
    for t in threading.enumerate():
        if t is not threading.current_thread() and t.daemon:
            t.join(2)
    msg = next(x for x in sent if isinstance(x, tuple) and x[0] == "msg")
    assert msg[1] == "budi@contoh.id" and "https://app.contoh.id/reset?token=" in msg[3] and "penyerang" not in msg[3]
    assert ("connect", "smtp.contoh.id", 587) in sent and "tls" in sent and ("login", "kirim@contoh.id", "pw") in sent


def test_seed_admin_is_idempotent_and_validates(monkeypatch):
    monkeypatch.setenv("TRENDWATCH_ADMIN_EMAIL", "admin@contoh.id")
    monkeypatch.setenv("TRENDWATCH_ADMIN_PASSWORD", "rahasia123")
    auth.seed_admin()
    auth.seed_admin()
    assert users.count() == 1 and auth.login("admin@contoh.id", "rahasia123", "1.2.3.4")["name"] == "Admin"
    monkeypatch.setenv("TRENDWATCH_ADMIN_PASSWORD", "baru-tapi-diabaikan")
    auth.seed_admin()
    assert auth.login("admin@contoh.id", "rahasia123", "1.2.3.4")        # tidak menimpa kata sandi yang ada
    monkeypatch.setenv("TRENDWATCH_ADMIN_EMAIL", "rusak")
    auth.seed_admin()
    assert users.count() == 1


# --------------------------------------------------------------------------- alur lewat API (perlu FastAPI)


@pytest.fixture()
def client():
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from app import main
    with TestClient(main.app, follow_redirects=False) as c:
        yield c


def test_api_requires_login_and_full_flow(client):
    assert client.get("/api/health").status_code == 200
    r = client.get("/?seed=cpns")
    assert r.status_code == 303 and r.headers["location"].startswith("/login?next=")
    for page in ("/login", "/daftar", "/lupa", "/reset"):
        assert client.get(page).status_code == 200
    assert client.get("/api/keywords?seed=a&demo=true").status_code == 401
    assert client.get("/api/session").json() == {"auth": True, "user": None}

    assert client.post("/api/register", json={"name": "Budi", "email": "budi@contoh.id", "password": "pendek"}).status_code == 400
    ok = client.post("/api/register", json={"name": "Budi", "email": "budi@contoh.id", "password": "rahasia123"})
    assert ok.status_code == 200 and "httponly" in ok.headers["set-cookie"].lower()
    assert client.get("/").status_code == 200 and client.get("/akun").status_code == 200
    assert client.get("/api/keywords?seed=cpns&demo=true").status_code == 200
    assert client.get("/login").status_code == 303
    assert client.get("/api/session").json()["user"] == {"name": "Budi", "email": "budi@contoh.id"}

    assert client.post("/api/password", json={"current": "salah", "new": "sandibaru99"}).status_code == 400
    assert client.post("/api/password", json={"current": "rahasia123", "new": "sandibaru99"}).status_code == 200
    assert client.get("/api/keywords?seed=cpns&demo=true").status_code == 200       # perangkat ini dapat sesi baru

    client.post("/api/logout")
    assert client.get("/api/keywords?seed=cpns&demo=true").status_code == 401
    bad = client.post("/api/login", json={"email": "budi@contoh.id", "password": "rahasia123", "next": "/"})
    assert bad.status_code == 401
    good = client.post("/api/login", json={"email": "budi@contoh.id", "password": "sandibaru99", "next": "//evil.com"})
    assert good.status_code == 200 and good.json()["next"] == "/"


def test_api_old_session_dies_when_password_changes_elsewhere(client):
    client.post("/api/register", json={"name": "B", "email": "b@c.id", "password": "rahasia123"})
    cookie = client.cookies.get(auth.COOKIE)
    from fastapi.testclient import TestClient

    from app import main
    other = TestClient(main.app, follow_redirects=False)
    other.cookies.set(auth.COOKIE, cookie)
    assert other.get("/api/keywords?seed=a&demo=true").status_code == 200
    client.post("/api/password", json={"current": "rahasia123", "new": "sandibaru99"})
    assert other.get("/api/keywords?seed=a&demo=true").status_code == 401


def test_api_login_throttle_forgot_and_reset(client, caplog):
    client.post("/api/register", json={"name": "B", "email": "b@c.id", "password": "rahasia123"})
    client.post("/api/logout")
    for _ in range(5):
        assert client.post("/api/login", json={"email": "b@c.id", "password": "x"}).status_code == 401
    r = client.post("/api/login", json={"email": "b@c.id", "password": "rahasia123"})
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0

    auth.reset_throttles()
    caplog.set_level("WARNING", logger="trendwatch.auth")
    assert client.post("/api/forgot", json={"email": "tidak@ada.id"}).status_code == 200
    assert client.post("/api/forgot", json={"email": "b@c.id"}).status_code == 200
    token = grab_link(caplog)
    assert client.post("/api/reset", json={"token": token, "password": "sandibaru99"}).status_code == 200
    assert client.post("/api/reset", json={"token": token, "password": "sandibaru99"}).status_code == 400
    assert client.post("/api/login", json={"email": "b@c.id", "password": "sandibaru99"}).status_code == 200


def test_login_disabled_switch(client, monkeypatch):
    monkeypatch.setenv("TRENDWATCH_AUTH", "off")
    assert client.get("/").status_code == 200 and client.get("/login").status_code == 303
    assert client.post("/api/login", json={}).status_code == 404
    assert client.get("/api/session").json() == {"auth": False, "user": None}


def test_accounts_persist_across_restart_and_default_db_ignores_cwd(tmp_path, monkeypatch):
    make()
    db.init_db(); users.init()                              # "restart": init ulang tidak menghapus akun
    assert auth.login("budi@contoh.id", "rahasia123", "1.1.1.1")["name"] == "Budi"
    monkeypatch.delenv("TRENDWATCH_DB")
    monkeypatch.chdir(tmp_path)
    assert db.db_path() == str(db.DEFAULT_DB) and db.DEFAULT_DB.is_absolute()


def test_halaman_masuk_bisa_diindeks_dan_dashboard_tidak(monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    monkeypatch.setenv("TRENDWATCH_BASE_URL", "https://contoh.example")
    monkeypatch.setenv("TRENDWATCH_GOOGLE_VERIFICATION", "abc123")
    from app import main
    c = TestClient(main.app)
    r = c.get("/login")
    assert r.status_code == 200
    assert "X-Robots-Tag" not in r.headers
    assert 'rel="canonical" href="https://contoh.example/login"' in r.text
    assert 'name="google-site-verification" content="abc123"' in r.text
    assert "Tentang Cerekey" in r.text
    assert c.get("/daftar").headers["X-Robots-Tag"].startswith("noindex")
    api = c.get("/api/overview")
    assert api.headers["X-Robots-Tag"].startswith("noindex")
    robots = c.get("/robots.txt")
    assert "Allow: /login" in robots.text and "Disallow: /api/" in robots.text
    assert "https://contoh.example/sitemap.xml" in robots.text
    assert "<loc>https://contoh.example/login</loc>" in c.get("/sitemap.xml").text
