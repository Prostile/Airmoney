from airmoney.steam.collections import build_exterior_variants, build_market_listing_url, steam_market_filter_params


def test_build_exterior_variants_replaces_existing_exterior():
    variants = build_exterior_variants("AK-47 | Test (Factory New)", ["Minimal Wear", "Field-Tested"])
    assert variants == ["AK-47 | Test (Minimal Wear)", "AK-47 | Test (Field-Tested)"]


def test_build_market_listing_url_encodes_market_hash_name():
    url = build_market_listing_url("AK-47 | Test (Factory New)")
    assert "steamcommunity.com/market/listings/730/" in url
    assert "%7C" in url


def test_steam_market_filter_params_use_item_characteristics():
    params = steam_market_filter_params(
        {
            "exterior": "Factory New",
            "is_souvenir": True,
            "is_stattrak": False,
        }
    )

    assert params["appid"] == "730"
    assert params["category_730_Exterior"] == "tag_WearCategory0"
    assert params["category_730_Quality"] == "tag_tournament"
