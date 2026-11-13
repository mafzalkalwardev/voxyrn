"""Authentication, CSRF, and admin user creation."""
from fastapi.testclient import TestClient

from src.auth import ensure_bootstrap_admin, reset_rate_limits
from src.web_app import app


def setup_function():
    reset_rate_limits()
    ensure_bootstrap_admin()


def test_anonymous_api_is_rejected():
    client = TestClient(app)
    response = client.get("/api/run/status")
    assert response.status_code == 401
    assert response.json()["detail"] == "Authentication required"


def test_anonymous_console_redirects_to_login():
    client = TestClient(app)
    response = client.get("/dashboard", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


def test_login_and_logout_round_trip():
    client = TestClient(app)
    denied = client.post("/api/settings", json={"agent_name": "Nope"})
    assert denied.status_code == 401

    login = client.post(
        "/login",
        data={"username": "admin", "password": "test-password-123"},
        follow_redirects=False,
    )
    assert login.status_code == 303
    csrf = client.cookies.get("voxyrn_csrf")
    assert csrf

    page = client.get("/dashboard")
    assert page.status_code == 200
    assert "Log out" in page.text

    missing = client.post("/api/run/stop")
    assert missing.status_code == 403

    stopped = client.post("/api/run/stop", headers={"X-CSRF-Token": csrf})
    assert stopped.status_code == 200

    client.post("/logout", data={"csrf_token": csrf}, follow_redirects=False)
    again = client.get("/dashboard", follow_redirects=False)
    assert again.status_code == 303


def test_bad_password_is_rejected():
    client = TestClient(app)
    response = client.post(
        "/login",
        data={"username": "admin", "password": "not-the-password"},
    )
    assert response.status_code == 401
    assert "not accepted" in response.text


def test_login_rate_limit():
    client = TestClient(app)
    last = None
    for _ in range(8):
        last = client.post(
            "/login",
            data={"username": "admin", "password": "wrong-password"},
        )
    assert last is not None
    assert last.status_code == 401
    locked = client.post(
        "/login",
        data={"username": "admin", "password": "test-password-123"},
    )
    assert locked.status_code == 429


def test_admin_can_create_operator():
    client = TestClient(app)
    login = client.post(
        "/login",
        data={"username": "admin", "password": "test-password-123"},
        follow_redirects=False,
    )
    assert login.status_code == 303
    csrf = client.cookies.get("voxyrn_csrf")
    created = client.post(
        "/admin/users",
        data={
            "username": "operator.one",
            "password": "operator-pass-1",
            "role": "operator",
            "csrf_token": csrf,
        },
    )
    assert created.status_code == 200
    assert "operator.one" in created.text

    other = TestClient(app)
    other_login = other.post(
        "/login",
        data={"username": "operator.one", "password": "operator-pass-1"},
        follow_redirects=False,
    )
    assert other_login.status_code == 303
    denied = other.get("/admin/users")
    assert denied.status_code == 403


def test_healthz_is_public():
    client = TestClient(app)
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
