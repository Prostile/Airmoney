from airmoney.steam.collections import build_exterior_variants, build_market_listing_url


def test_build_exterior_variants_replaces_existing_exterior():
    variants = build_exterior_variants("AK-47 | Test (Factory New)", ["Minimal Wear", "Field-Tested"])
    assert variants == ["AK-47 | Test (Minimal Wear)", "AK-47 | Test (Field-Tested)"]


def test_build_market_listing_url_encodes_market_hash_name():
    url = build_market_listing_url("AK-47 | Test (Factory New)")
    assert "steamcommunity.com/market/listings/730/" in url
    assert "%7C" in url
