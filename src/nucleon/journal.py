"""Opt-in mail journal with pluggable storage. Metadata and hashes only."""

from __future__ import annotations

import os
from typing import Protocol

from nucleon.config import JournalConfig
from nucleon.models import JournalEntry


class JournalStore(Protocol):
    """Storage seam: remote backends implement this later."""

    def append(self, entry: JournalEntry) -> None: ...

    def entries(self) -> list[JournalEntry]: ...


class FileJournalStore:
    """Append-only JSONL file."""

    def __init__(self, path: str) -> None:
        self._path = path

    def append(self, entry: JournalEntry) -> None:
        parent = os.path.dirname(self._path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(entry.model_dump_json() + "\n")

    def entries(self) -> list[JournalEntry]:
        if not os.path.exists(self._path):
            return []
        with open(self._path, encoding="utf-8") as f:
            return [
                JournalEntry.model_validate_json(line) for line in f if line.strip()
            ]


class Journal(Protocol):
    def record(self, entry: JournalEntry) -> None: ...

    def has_processed(self, ref_key: str) -> bool: ...

    def mark_sent(self, ref_key: str) -> None: ...

    def drafted_unsent(self) -> list[JournalEntry]: ...


class StoredJournal:
    def __init__(self, store: JournalStore) -> None:
        self._store = store

    def record(self, entry: JournalEntry) -> None:
        self._store.append(entry)

    def has_processed(self, ref_key: str) -> bool:
        return any(e.ref_key == ref_key for e in self._store.entries())

    def mark_sent(self, ref_key: str) -> None:
        matches = [e for e in self._store.entries() if e.ref_key == ref_key]
        if not matches:
            return
        self._store.append(
            matches[-1].model_copy(update={"action": "marked_sent", "status": "sent"})
        )

    def drafted_unsent(self) -> list[JournalEntry]:
        entries = self._store.entries()
        sent = {e.ref_key for e in entries if e.action == "marked_sent"}
        return [e for e in entries if e.action == "drafted" and e.ref_key not in sent]


class NullJournal:
    def record(self, entry: JournalEntry) -> None:
        return None

    def has_processed(self, ref_key: str) -> bool:
        return False

    def mark_sent(self, ref_key: str) -> None:
        return None

    def drafted_unsent(self) -> list[JournalEntry]:
        return []


def build_journal(cfg: JournalConfig) -> Journal:
    if not cfg.enabled:
        return NullJournal()
    if cfg.backend == "file":
        if cfg.path is None:
            raise ValueError("journal.path is required when journal is enabled with the file backend")
        return StoredJournal(FileJournalStore(cfg.path))
    raise ValueError(f"unknown journal backend: {cfg.backend}")
