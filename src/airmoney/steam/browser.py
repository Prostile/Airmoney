from __future__ import annotations


class SteamAccessLimited(Exception):
    pass


def check_steam_access(page, response=None) -> None:
    if response is not None:
        status = response.status
        if status == 403:
            raise SteamAccessLimited("Steam вернул 403 Forbidden.")
        if status == 429:
            raise SteamAccessLimited("Steam вернул 429 Too Many Requests.")
        if 500 <= status <= 599:
            raise SteamAccessLimited(f"Steam вернул серверную ошибку {status}.")

    markers = [
        "captcha",
        "are you a human",
        "verify you are human",
        "access denied",
        "too many requests",
        "automated requests",
        "unusual traffic",
        "temporarily unavailable",
        "please try again later",
        "request has been rejected",
    ]

    try:
        text = ""
        text += page.url.lower()
        text += "\n" + page.title().lower()
        text += "\n" + page.locator("body").inner_text(timeout=1200).lower()
        text = text[:15000]
        for marker in markers:
            if marker in text:
                raise SteamAccessLimited(f"Обнаружен маркер ограничения Steam: {marker}")
    except SteamAccessLimited:
        raise
    except Exception:
        pass


def block_unneeded_requests(route) -> None:
    if route.request.resource_type in ["image", "font", "media"]:
        route.abort()
    else:
        route.continue_()


def close_cookie_banner(page) -> bool:
    possible_buttons = [
        "button:has-text('Принять все')",
        "button:has-text('Accept All')",
        "button:has-text('Accept all')",
        "text=Принять все",
        "text=Accept All",
        "text=Accept all",
    ]
    for selector in possible_buttons:
        try:
            locator = page.locator(selector).first
            if locator.count() > 0:
                locator.click(timeout=1200)
                page.wait_for_timeout(250)
                return True
        except Exception:
            continue
    return False
