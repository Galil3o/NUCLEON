"""Stable internal account identity (R-012).

Every account configuration carries an operator-chosen, stable ``id``. All
state (processing marker, journal, contacts, reconcile) is bound to the hash
of THAT id, never to the login name: the same login on two hosts therefore
cannot collide, and changing a login/server does not silently re-key (or
silently inherit) state. A change of login or server under an existing id is
an explicit decision (``mail.accept_identity_change``), not an accident.

Only hashes are persisted; no host, login or address ever reaches the registry.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterable
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:  # pragma: no cover
    from nucleon.config import Config, MailConfig

ACCOUNT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class DuplicateAccountIdError(ValueError):
    """Two account configurations use the same internal id."""


class AccountIdentityChanged(Exception):
    """Login or server changed under an existing account id; needs a decision."""


def account_hash(account_id: str) -> str:
    """Namespace hash every ref/marker/journal entry of this account carries."""
    return hashlib.sha256(b"nucleon-account:v1:" + account_id.encode("utf-8")).hexdigest()


def account_hash_for(cfg: "Config") -> str:
    return account_hash(cfg.mail.id)


def validate_unique_account_ids(ids: Iterable[str]) -> None:
    """Reject duplicate ids (case-insensitive, so 'Work' and 'work' clash)."""
    seen: set[str] = set()
    for i in ids:
        folded = i.casefold()
        if folded in seen:
            raise DuplicateAccountIdError(f"duplicate account id: {i!r}")
        seen.add(folded)


def fingerprint(mail: "MailConfig") -> str:
    """Hash of what makes the account 'the same mailbox': server + login."""
    raw = f"{mail.host.lower()}|{mail.port}|{mail.username}".encode("utf-8")
    return hashlib.sha256(b"nucleon-fp:v1:" + raw).hexdigest()


class AccountRegistry:
    """Maps account id -> identity fingerprint (hashes only), durable JSON file."""

    def __init__(self, path: str) -> None:
        self._path = path

    def _load(self) -> dict[str, str]:
        try:
            with open(self._path, encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return {}
        except (ValueError, OSError) as e:
            raise AccountIdentityChanged(
                f"account registry {self._path} is unreadable; refusing to guess"
            ) from e
        if not isinstance(data, dict):
            raise AccountIdentityChanged(f"account registry {self._path} is malformed")
        return {str(k): str(v) for k, v in data.items()}

    def _save(self, data: dict[str, str]) -> None:
        parent = os.path.dirname(self._path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = self._path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self._path)

    def check(self, cfg: "Config") -> Literal["new", "unchanged", "changed-accepted"]:
        """Record first use; refuse a changed login/server unless explicitly accepted."""
        data = self._load()
        aid, fp = cfg.mail.id, fingerprint(cfg.mail)
        known = data.get(aid)
        if known is None:
            data[aid] = fp
            self._save(data)
            return "new"
        if known == fp:
            return "unchanged"
        if cfg.mail.accept_identity_change:
            data[aid] = fp
            self._save(data)
            return "changed-accepted"
        raise AccountIdentityChanged(
            f"account {aid!r}: login or server differs from the one this account's "
            "state (marker, journal, contacts) was built for. Existing state would "
            "be reused for a different mailbox. If that is intended, set "
            "mail.accept_identity_change: true for one run; otherwise use a new id."
        )
