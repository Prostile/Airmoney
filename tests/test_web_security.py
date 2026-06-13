import base64

from fastapi.testclient import TestClient

from airmoney.web.app import create_app
from airmoney.storage.repositories import Repository


def _auth_header(user: str = "admin", password: str = "secret") -> dict[str, str]:
    token = base64.b64encode(f"{user}:{password}".encode("ascii")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def test_docs_and_openapi_are_not_public(tmp_path, monkeypatch):
    monkeypatch.setenv("AIRMONEY_WEB_USER", "admin")
    monkeypatch.setenv("AIRMONEY_WEB_PASSWORD", "secret")
    app = create_app(Repository(tmp_path / "test.sqlite3"))
    with TestClient(app) as client:
        assert client.get("/docs").status_code == 404
        assert client.get("/openapi.json").status_code == 404


def test_api_status_requires_basic_auth(tmp_path, monkeypatch):
    monkeypatch.setenv("AIRMONEY_WEB_USER", "admin")
    monkeypatch.setenv("AIRMONEY_WEB_PASSWORD", "secret")
    app = create_app(Repository(tmp_path / "test.sqlite3"))
    with TestClient(app) as client:
        assert client.get("/api/status").status_code == 401
        response = client.get("/api/status", headers=_auth_header())
        assert response.status_code == 200
        assert response.json()["parser_enabled"] is False
        assert "stats" in response.json()
        assert "scan_summary" in response.json()


def test_dashboard_uses_scheme_relative_static_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("AIRMONEY_WEB_USER", "admin")
    monkeypatch.setenv("AIRMONEY_WEB_PASSWORD", "secret")
    app = create_app(Repository(tmp_path / "test.sqlite3"))
    with TestClient(app) as client:
        response = client.get("/dashboard", headers=_auth_header())
    assert response.status_code == 200
    assert 'href="/static/vendor/tabler/tabler.min.css"' in response.text
    assert 'src="/static/vendor/htmx/htmx.min.js"' in response.text
    assert 'href="/static/dashboard.css"' in response.text
    assert 'src="/static/app.js"' in response.text
    assert "http://testserver/static/" not in response.text


def test_settings_can_reset_steam_guard_cooldown(tmp_path, monkeypatch):
    monkeypatch.setenv("AIRMONEY_WEB_USER", "admin")
    monkeypatch.setenv("AIRMONEY_WEB_PASSWORD", "secret")
    repo = Repository(tmp_path / "test.sqlite3")
    repo.set_steam_guard_state(
        cooldown_until="2099-01-01T00:00:00+00:00",
        reason="access_limited",
        consecutive_blocks=3,
        last_error_at="2098-12-31T23:59:00+00:00",
    )
    app = create_app(repo)

    with TestClient(app) as client:
        settings_response = client.get("/settings", headers=_auth_header())
        reset_response = client.post(
            "/settings/steam-guard/reset",
            headers=_auth_header(),
            follow_redirects=False,
        )

    assert settings_response.status_code == 200
    assert "Reset cooldown" in settings_response.text
    assert "2099-01-01T00:00:00+00:00" in settings_response.text
    assert reset_response.status_code == 303

    state = repo.get_steam_guard_state()
    assert state["steam_cooldown_until"] == ""
    assert state["steam_cooldown_reason"] == ""
    assert state["steam_consecutive_blocks"] == 0
    assert state["last_steam_error_at"] == ""
