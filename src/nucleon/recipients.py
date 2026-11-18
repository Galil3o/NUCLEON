"""Recipient resolution: the boundary between model proposals and headers (R-009).

Model-proposed recipients resolve ONLY against account-bound, approved contacts
(the config contact list, plus opt-in history-learned addresses from a separate
store). Everything else is *review-required*: it is never placed in a header and
never learned automatically. Every final recipient carries its provenance.
"""
from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel, ConfigDict

from nucleon.addresses import normalize_address
from nucleon.appendlog import append_line
from nucleon.models import MessageMeta, RecipientLine

if TYPE_CHECKING:  # pragma: no cover
    from nucleon.config import Config, RecipientsConfig


class LearnedEntry(BaseModel):
    """One opt-in, history-learned address. Sensitive: lives in its own store,
    bound to one account id, with a mandatory (pseudonymised) origin."""

    model_config = ConfigDict(frozen=True)

    account_id: str
    address: str
    origin: str
    ts: datetime


class LearnedStore(Protocol):
    def append(self, entry: LearnedEntry) -> None: ...

    def entries(self) -> list[LearnedEntry]: ...


class FileLearnedStore:
    """Append-only JSONL, deliberately separate from marker and journal."""

    def __init__(self, path: str) -> None:
        self._path = path

    def append(self, entry: LearnedEntry) -> None:
        append_line(self._path, entry.model_dump_json())  # R-013

    def entries(self) -> list[LearnedEntry]:
        if not os.path.exists(self._path):
            return []
        out: list[LearnedEntry] = []
        with open(self._path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    try:
                        out.append(LearnedEntry.model_validate_json(line))
                    except ValueError:
                        continue  # tolerate a corrupt/partial line
        return out


def learn(store: LearnedStore, account_id: str, address: str, origin: str) -> LearnedEntry:
    """Seam for the (future) history-learning step; an explicit, traceable add.

    Never called by the gate or engine: review-required addresses are not
    auto-learned. ``origin`` (e.g. ``seen-in:<pseudonym>``) is mandatory.
    """
    addr = normalize_address(address)
    if addr is None:
        raise ValueError("not a single valid address")
    if not origin or len(origin) > 128 or any(ord(c) < 0x20 or ord(c) == 0x7F for c in origin):
        raise ValueError("a traceable origin is required")
    entry = LearnedEntry(account_id=account_id, address=addr, origin=origin,
                         ts=datetime.now(timezone.utc))
    store.append(entry)
    return entry


@dataclass(frozen=True)
class Contact:
    address: str
    source: str  # "contact" (config) | "learned"
    origin: str


class ContactBook:
    """Approved contacts of ONE account."""

    def __init__(
        self,
        account_id: str,
        contacts: Iterable[str] = (),
        learned_store: LearnedStore | None = None,
        learned_enabled: bool = False,
    ) -> None:
        self._account = account_id
        self._config = {a for a in (normalize_address(c) for c in contacts) if a}
        self._learned: dict[str, str] = {}
        if learned_enabled and learned_store is not None:  # opt-in, OFF by default
            for e in learned_store.entries():
                if e.account_id == account_id:  # never another account's addresses
                    self._learned.setdefault(e.address, e.origin)

    @classmethod
    def from_config(cls, cfg: "Config", learned_store: LearnedStore | None = None) -> "ContactBook":
        r = cfg.recipients
        return cls(cfg.mail.id, r.contacts, learned_store, r.learned_enabled)

    def lookup(self, address: str) -> Contact | None:
        a = normalize_address(address)
        if a is None:
            return None
        if a in self._config:
            return Contact(a, "contact", "config")
        if a in self._learned:
            return Contact(a, "learned", self._learned[a])
        return None


@dataclass(frozen=True)
class Resolution:
    recipients: tuple[RecipientLine, ...]
    review_required: tuple[str, ...]
    differs_from_sender: bool


def own_addresses(cfg: "Config") -> frozenset[str]:
    own = {a for a in (normalize_address(x) for x in cfg.recipients.own_addresses) if a}
    login = normalize_address(cfg.mail.username)
    if login:
        own.add(login)
    return frozenset(own)


def resolve(
    meta: MessageMeta,
    proposed: Iterable[str],
    cfg: "Config",
    book: ContactBook,
) -> Resolution:
    rcfg: "RecipientsConfig" = cfg.recipients
    own = own_addresses(cfg)
    sender = normalize_address(meta.from_addr)
    reply_to = normalize_address(meta.reply_to) if meta.reply_to else None
    lines: list[RecipientLine] = []
    seen: set[str] = set()

    base = reply_to or sender  # DECISIONS section 4: To = Reply-To if present, else From
    if base:
        src = "reply-to" if (reply_to and reply_to != sender) else "from"
        lines.append(RecipientLine(address=base, source=src, role="to"))
        seen.add(base)

    if rcfg.mode in ("sender-and-recipients",):
        for raw in [*meta.to_addrs, *meta.cc_addrs]:
            a = normalize_address(raw)
            if a and a not in seen and a not in own:
                lines.append(RecipientLine(address=a, source="original-recipient", role="cc"))
                seen.add(a)

    review: list[str] = []
    if rcfg.mode == "model-decided":
        for raw in proposed:
            a = normalize_address(raw)
            if a is None or a in seen or a in own:
                continue  # malformed: dropped; already present / own: nothing to add
            contact = book.lookup(a)
            if contact is None:
                if a not in review:
                    review.append(a)  # unknown: review-required, NOT a recipient
                continue
            lines.append(RecipientLine(address=a, source=contact.source, role="cc",
                                       origin=contact.origin))
            seen.add(a)

    final = {r.address for r in lines}
    differs = bool(review) or final != ({sender} if sender else set())
    return Resolution(tuple(lines), tuple(review), differs)
