from airmoney.steam.scanner import _browser_proxy_config


def test_browser_proxy_config_uses_explicit_airmoney_proxy(monkeypatch):
    monkeypatch.setenv("AIRMONEY_BROWSER_PROXY", "http://127.0.0.1:10808")

    assert _browser_proxy_config() == {"server": "http://127.0.0.1:10808"}


def test_browser_proxy_config_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("AIRMONEY_BROWSER_PROXY", raising=False)

    assert _browser_proxy_config() is None
