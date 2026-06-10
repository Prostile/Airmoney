import base64

from fastapi.testclient import TestClient

from airmoney.config.models import Collection, ItemDefinition, MarketListing, utc_now_iso
from airmoney.recommendation.engine import evaluate_listing
from airmoney.storage.repositories import Repository
from airmoney.web.app import create_app


def _auth_header(user: str = "admin", password: str = "secret") -> dict[str, str]:
    token = base64.b64encode(f"{user}:{password}".encode("ascii")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def test_open_candidate_marks_new_candidate_as_opened(tmp_path, monkeypatch):
    monkeypatch.setenv("AIRMONEY_WEB_USER", "admin")
    monkeypatch.setenv("AIRMONEY_WEB_PASSWORD", "secret")
    repo = Repository(tmp_path / "test.sqlite3")
    repo.save_collection(Collection(id="c1", name="Collection"))
    repo.save_item(ItemDefinition(id="i1", collection_id="c1", market_hash_name="Skin A"))
    rule = repo.get_rule_for_item("i1")
    now = utc_now_iso()
    listing = MarketListing(
        id="listing_open",
        item_definition_id="i1",
        rule_id=rule["id"],
        skin_name="Skin A",
        listing_url="https://steamcommunity.com/market/listings/730/test",
        search_url="https://steamcommunity.com/market/search?q=test",
        buy_price_rub=1000,
        first_seen_at=now,
        last_seen_at=now,
    )
    repo.save_listing(listing)
    candidate = evaluate_listing(
        listing_id=listing.id,
        buy_price_rub=1000,
        float_value=None,
        pattern=None,
        rule={**rule, "target_resale_price_rub": 1500},
        settings=repo.get_settings(),
    )
    repo.save_candidate(candidate)
    app = create_app(repo)
    with TestClient(app) as client:
        response = client.get(
            f"/candidates/{candidate.id}/open?target=listing",
            headers=_auth_header(),
            follow_redirects=False,
        )
    assert response.status_code == 303
    assert response.headers["location"] == listing.listing_url
    assert repo.get_candidate_details(candidate.id)["status"] == "opened"
