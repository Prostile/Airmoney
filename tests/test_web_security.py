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
