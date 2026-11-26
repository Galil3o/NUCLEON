"""DataMap: indexes DriveRecords by claimed sha1 for the attachment-presence check."""
from __future__ import annotations

from typing import Iterable

from proton_drive_kit.records import DriveRecord


class DataMap:
    def __init__(self) -> None:
        self._records: list[DriveRecord] = []
        self._by_sha1: dict[str, list[str]] = {}

    def add(self, rec: DriveRecord) -> None:
        self._records.append(rec)
        if rec.is_folder or not rec.sha1:
            return
        # Normalize to lowercase: a backend that reports sha1 in uppercase must still match an
        # attachment's lowercase hexdigest(), otherwise a real match reads as a false 'absent'.
        self._by_sha1.setdefault(rec.sha1.lower(), []).append(rec.path)

    def add_all(self, recs: Iterable[DriveRecord]) -> None:
        for r in recs:
            self.add(r)

    def paths_for_sha1(self, sha1: str) -> list[str]:
        if not sha1:
            return []
        return list(self._by_sha1.get(sha1.lower(), []))

    def contains_sha1(self, sha1: str, under: str | None = None) -> list[str]:
        paths = self.paths_for_sha1(sha1)
        if under is None:
            return paths
        base = under.rstrip("/")
        prefix = base + "/"
        return [p for p in paths if p == base or p.startswith(prefix)]

    def has_digestless(self, under: str | None = None) -> bool:
        """Are there file records in scope that carry no sha1? A hash miss within
        such a scope is inconclusive (the match could be one of these unindexed
        files), so a presence check must report 'unknown' rather than 'absent'."""
        base = under.rstrip("/") if under is not None else None
        for r in self._records:
            if r.is_folder or r.sha1:
                continue
            if base is None or r.path == base or r.path.startswith(base + "/"):
                return True
        return False

    def records(self) -> list[DriveRecord]:
        return list(self._records)

    def __len__(self) -> int:
        return sum(len(v) for v in self._by_sha1.values())
