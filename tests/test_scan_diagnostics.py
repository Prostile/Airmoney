from airmoney.analytics.scan_diagnostics import build_scan_diagnostics, render_scan_diagnostics
from airmoney.config.models import Collection, ItemDefinition
from airmoney.storage.repositories import Repository


def test_scan_diagnostics_explains_exact_match_rejections(tmp_path):
    repo = Repository(tmp_path / "test.sqlite3")
    repo.save_collection(Collection(id="c1", name="Collection"))
    repo.save_item(
        ItemDefinition(
            id="i0",
            collection_id="c1",
            market_hash_name="Souvenir P90 | Facility Negative (Factory New)",
            exterior="Factory New",
            is_souvenir=True,
        )
    )
    run_id = repo.start_scan_run("test")
    repo.save_item_scan_failure(
        run_id,
        "i0",
        {"status": "no_exact_cards", "cards_seen": 20, "exact_cards": 0, "error": "No exact-match cards parsed"},
    )
    repo.log_user_action(
        "steam_scan",
        "i0",
        "exact_match_rejected",
        {
            "target": "P90 | Facility Negative",
            "rejected_count": 20,
            "examples": [{"skin_name": "P90 | Facility Negative (Field-Tested)", "price_rub": 25.11}],
        },
    )
    repo.finish_scan_run(run_id, "success", scanned_items=1, listings_saved=0, candidates_saved=0)

    report = build_scan_diagnostics(repo, limit=1)
    rendered = render_scan_diagnostics(report)

    assert report["runs"][0]["cards_seen"] == 20
    assert report["runs"][0]["exact_cards"] == 0
    assert report["runs"][0]["rejected_count"] == 20
    assert "strict exact-match rejects" in report["runs"][0]["verdict"]
    assert "Field-Tested" in rendered
