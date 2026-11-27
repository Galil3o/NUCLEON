"""DataMap: indexes DriveRecords by claimed sha1 for the attachment-presence check."""
from __future__ import annotations

import re
from typing import Iterable

from proton_drive_kit.records import DriveRecord
from proton_drive_kit.scanner import ScanIncomplete

_SHA1_RE = re.compile(r"\A[0-9a-fA-F]{40}\Z")


def _is_comparable_sha1(value) -> bool:
    """A usable content digest is a well-formed 40-hex sha1. A missing OR format-invalid
    provider digest is not comparable, so it must not be indexed and a hash miss against a
    scope that holds such a record stays inconclusive (unknown), never 'absent' (R-021)."""
    return bool(value) and _SHA1_RE.match(value) is not None


class DataMap:
    def __init__(self) -> None:
        self._records: list[DriveRecord] = []
        self._by_sha1: dict[str, list[str]] = {}
        self._incomplete: list[ScanIncomplete] = []

    def add(self, rec: DriveRecord) -> None:
        self._records.append(rec)
        if rec.is_folder or not _is_comparable_sha1(rec.sha1):
            return
        # Normalize to lowercase: a backend that reports sha1 in uppercase must still match an
        # attachment's lowercase hexdigest(), otherwise a real match reads as a false 'absent'.
        self._by_sha1.setdefault(rec.sha1.lower(), []).append(rec.path)

    def add_all(self, items: Iterable) -> None:
        """Index DriveRecords and capture ScanIncomplete markers (un-scanned regions).
        ``scan()`` yields records and incompleteness markers in one stream; the markers
        must not be dropped, since they drive 'unknown' vs 'absent' in a presence check."""
        for it in items:
            if isinstance(it, ScanIncomplete):
                self._incomplete.append(it)
            else:
                self.add(it)

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
            if r.is_folder or _is_comparable_sha1(r.sha1):
                continue
            if base is None or r.path == base or r.path.startswith(base + "/"):
                return True
        return False

    def coverage_incomplete(self, under: str | None = None) -> bool:
        """True if an un-scanned region (a captured ScanIncomplete) could overlap
        ``under``, so a hash miss there cannot be proven 'absent'. ``under=None`` (the
        whole map) is incomplete if any region is. Otherwise an un-scanned folder ``I``
        forces 'unknown' when it IS the scope, lies WITHIN it, or CONTAINS it; a disjoint
        sibling region does not."""
        if not self._incomplete:
            return False
        if under is None:
            return True
        base = under.rstrip("/")
        for inc in self._incomplete:
            ip = inc.path.rstrip("/")
            if ip == base or ip.startswith(base + "/") or base.startswith(ip + "/"):
                return True
        return False

    def incomplete_scopes(self) -> list[ScanIncomplete]:
        """The captured un-scanned regions (folder path, uid, reason)."""
        return list(self._incomplete)

    def records(self) -> list[DriveRecord]:
        return list(self._records)

    def __len__(self) -> int:
        return sum(len(v) for v in self._by_sha1.values())
