import base64

from fastapi.testclient import TestClient

from airmoney.config.models import Collection, ItemDefinition
from airmoney.storage.repositories import Repository
from airmoney.web.app import create_app


def _auth_header(user: str = "admin", password: str = "secret") -> dict[str, str]:
    token = base64.b64encode(f"{user}:{password}".encode("ascii")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def test_items_page_renders_dropdowns_and_preserves_legacy_values(tmp_path, monkeypatch):
    monkeypatch.setenv("AIRMONEY_WEB_USER", "admin")
    monkeypatch.setenv("AIRMONEY_WEB_PASSWORD", "secret")
    repo = Repository(tmp_path / "test.sqlite3")
    repo.save_collection(Collection(id="c1", name="Collection"))
    repo.save_item(
        ItemDefinition(
            id="i1",
            collection_id="c1",
            market_hash_name="Skin A",
            exterior="Legacy Exterior",
            rarity="Legacy Rarity",
            quality="Legacy Quality",
        )
    )
    app = create_app(repo)

    with TestClient(app) as client:
        response = client.get("/items", headers=_auth_header())

    assert response.status_code == 200
    text = response.text
    assert 'name="exterior"' in text
    assert 'name="rarity"' in text
    assert 'name="quality"' in text
    assert '<option value="Legacy Exterior" selected>Legacy Exterior</option>' in text
    assert '<option value="Legacy Rarity" selected>Legacy Rarity</option>' in text
    assert '<option value="Legacy Quality" selected>Legacy Quality</option>' in text
    assert "Consumer Grade" in text
    assert "Factory New" in text
    assert "Souvenir" in text


def test_items_page_paginates_records_by_100(tmp_path, monkeypatch):
    monkeypatch.setenv("AIRMONEY_WEB_USER", "admin")
    monkeypatch.setenv("AIRMONEY_WEB_PASSWORD", "secret")
    repo = Repository(tmp_path / "test.sqlite3")
    repo.save_collection(Collection(id="c1", name="Collection"))
    for index in range(105):
        repo.save_item(
            ItemDefinition(
                id=f"i{index:03d}",
                collection_id="c1",
                market_hash_name=f"Paged Skin {index:03d}",
                display_name=f"Paged Skin {index:03d}",
            )
        )
    app = create_app(repo)

    with TestClient(app) as client:
        first_page = client.get("/items", headers=_auth_header())
        second_page = client.get("/items?page=2", headers=_auth_header())

    assert first_page.status_code == 200
    assert "Paged Skin 000" in first_page.text
    assert "Paged Skin 099" in first_page.text
    assert "Paged Skin 100" not in first_page.text
    assert "Page 1 / 2" in first_page.text

    assert second_page.status_code == 200
    assert "Paged Skin 000" not in second_page.text
    assert "Paged Skin 100" in second_page.text
    assert "Paged Skin 104" in second_page.text
    assert "Page 2 / 2" in second_page.text
