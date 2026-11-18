"""Strict single-address normalisation for anything that may reach a header.

Model-proposed or header-derived addresses are never trusted as-is: exactly one
plain address (optionally with a display name), ASCII, no control characters,
no list separators. Anything else is rejected, so it can neither inject a
header line (``\\nBcc: ...``) nor smuggle a second recipient.
"""
from __future__ import annotations

import re
from email.utils import getaddresses, parseaddr

_ADDR_RE = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+$"
)


def normalize_address(raw: object) -> str | None:
    """Return the lower-cased bare address, or None when ``raw`` is not exactly one."""
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s or len(s) > 320 or any(ord(c) < 0x20 or ord(c) == 0x7F or ord(c) > 0x7E for c in s):
        return None
    if len(getaddresses([s])) != 1:
        return None
    addr = parseaddr(s)[1].strip()
    if not addr or len(addr) > 254 or not _ADDR_RE.fullmatch(addr):
        return None
    # The whole input must be the address, or `Name <address>`: nothing else.
    if s.casefold() != addr.casefold() and not re.fullmatch(
        r"[^<>]*<" + re.escape(addr) + r">", s, re.IGNORECASE
    ):
        return None
    return addr.lower()
