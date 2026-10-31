"""The only external URLs PrintMedic will ever open, always in the user's browser and only on request.

PrintMedic itself makes no internet requests. Opening one of these pages is an explicit,
user-confirmed action that hands the URL to the default browser.
"""

from __future__ import annotations

from urllib.parse import urlsplit

ALLOWED_DOMAINS: frozenset[str] = frozenset({
    "support.hp.com",
    "global.canon",
    "epson.com",
    "support.brother.com",
    "www.support.xerox.com",
    "www.kyoceradocumentsolutions.com",
    "support.lexmark.com",
    "www.ricoh.com",
    "support.microsoft.com",
    "www.pwg.org",
})


def is_allowed_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    return (
        parts.scheme == "https"
        and not parts.username and not parts.password
        and parts.port in (None, 443)
        and host in ALLOWED_DOMAINS
    )
