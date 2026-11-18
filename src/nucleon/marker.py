"""Minimal, always-on, durable processing marker (R-003).

Separate from, and simpler than, the opt-in audit journal: it records only
"this message identity was handled" plus completed sub-steps, so repeated runs
are idempotent even with the journal off (NullJournal / default config). The
journal sits on top of it (journal ⊇ marker); the marker never depends on it.

Metadata and hashes only: the identity is ``MessageRef.processing_key()``
(account hash, folder, UIDVALIDITY, UID, Message-ID hash). No message content.
Storage is pluggable like the journal's.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

from nucleon.config import MarkerConfig


class MarkerEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    # "started" is written before any mailbox write so that a crash mid-action
    # can never lead to a duplicate draft on the next run.
    status: Literal["started", "ok", "error"]
    steps: tuple[str, ...] = ()
    ts: datetime


class MarkerStore(Protocol):
    """Storage seam: remote backends implement this later."""

    def append(self, entry: MarkerEntry) -> None: ...

    def entries(self) -> list[MarkerEntry]: ...


class FileMarkerStore:
    """Append-only JSONL file; each append is flushed and fsynced (durable)."""

    def __init__(self, path: str) -> None:
        self._path = path

    def append(self, entry: MarkerEntry) -> None:
        parent = os.path.dirname(self._path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(entry.model_dump_json() + "\n")
            f.flush()
            os.fsync(f.fileno())

    def entries(self) -> list[MarkerEntry]:
        if not os.path.exists(self._path):
            return []
        out: list[MarkerEntry] = []
        with open(self._path, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    out.append(MarkerEntry.model_validate_json(line))
                except ValueError:
                    continue  # tolerate a corrupt/partial line
        return out


class ProcessingMarker:
    def __init__(self, store: MarkerStore) -> None:
        self._store = store
        self._latest: dict[str, MarkerEntry] = {e.key: e for e in store.entries()}

    def is_done(self, key: str) -> bool:
        """True for any recorded status, including an interrupted "started"."""
        return key in self._latest

    def entry(self, key: str) -> MarkerEntry | None:
        return self._latest.get(key)

    def mark(
        self, key: str, status: Literal["started", "ok", "error"], steps: tuple[str, ...] = ()
    ) -> None:
        entry = MarkerEntry(key=key, status=status, steps=steps, ts=datetime.now(timezone.utc))
        self._store.append(entry)  # durable first, then visible
        self._latest[key] = entry


def build_marker(cfg: MarkerConfig) -> ProcessingMarker:
    """There is deliberately no way to switch the marker off."""
    if cfg.backend == "file":
        return ProcessingMarker(FileMarkerStore(cfg.path))
    raise ValueError(f"unknown marker backend: {cfg.backend}")
