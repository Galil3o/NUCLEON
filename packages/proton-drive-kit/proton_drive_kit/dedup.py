"""Dedup planning over DriveRecords.

Turns duplicate groups (files sharing the same content sha1) into keep / delete /
review decisions under configurable rules. Pure and read-only: it produces a plan
and touches no Drive. Domain policy (which areas are protected, what counts as a
"copy" location) is passed in as parameters, never hard-coded.

Decision reasons, first match wins per group of distinct folders:
  - "same-folder": the whole group lives in one folder -> keep one, delete the rest
    (exact duplicates in one place; safe regardless of area).
  - "protect": copies span a protected prefix (e.g. "/CASES") and unprotected
    areas -> keep the protected one, delete the unprotected copies.
  - "clear-copy": a copy sits in a copy-labelled location (Archive/Sent/Copy/...)
    next to a non-copy original -> keep the original, delete the copy.
  - "intra-area" (medium): all copies within one top-level area, none protected ->
    keep the shortest path, delete the rest.
  - "review" (low): anything else (e.g. several copies inside a protected area, or
    cross-area with no protection signal) -> keep one, leave the rest for review.

Extra copies in the SAME folder are always added to ``delete`` (exact duplicates),
even inside a review group.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from proton_drive_kit.records import DriveRecord

DEFAULT_COPY_PATTERNS: tuple[str, ...] = (
    r"archiv", r"backup", r"kopie", r"\bcopy\b", r"versand", r"gesendet",
    r"\bsent\b", r"outbox", r"papierkorb", r"trash", r"vf-?drop", r"duplikat",
    r"recovery", r"autorecover",
)


@dataclass(frozen=True)
class DedupDecision:
    sha1: str
    keep: DriveRecord
    delete: tuple[DriveRecord, ...]
    review: tuple[DriveRecord, ...]
    reason: str       # same-folder | protect | clear-copy | intra-area | review
    confidence: str   # high | medium | low

    @property
    def reclaimable_bytes(self) -> int:
        return sum(r.size or 0 for r in self.delete)


def _parent(path: str) -> str:
    s = path.strip("/")
    return "/" + s.rsplit("/", 1)[0] if "/" in s else "/"


def _top(path: str) -> str:
    s = path.strip("/")
    return s.split("/", 1)[0] if s else ""


def find_duplicate_groups(records: Iterable[DriveRecord]) -> dict[str, list[DriveRecord]]:
    """Map content sha1 -> records sharing it, only for groups of 2+ files.
    Folders and records without a sha1 are ignored."""
    groups: dict[str, list[DriveRecord]] = {}
    for r in records:
        if r.is_folder or not r.sha1:
            continue
        groups.setdefault(r.sha1, []).append(r)
    return {s: rs for s, rs in groups.items() if len(rs) > 1}


def plan_dedup(
    records: Iterable[DriveRecord],
    *,
    protect_prefixes: tuple[str, ...] = (),
    copy_patterns: tuple[str, ...] = DEFAULT_COPY_PATTERNS,
) -> list[DedupDecision]:
    copy_re = re.compile("|".join(copy_patterns), re.IGNORECASE) if copy_patterns else None
    prefixes = tuple(p.rstrip("/") for p in protect_prefixes)

    def is_copy(path: str) -> bool:
        return bool(copy_re) and any(copy_re.search(seg) for seg in path.strip("/").split("/"))

    def is_protected(path: str) -> bool:
        return any(path == pre or path.startswith(pre + "/") for pre in prefixes)

    def rank(r: DriveRecord) -> tuple:
        # most canonical first: protected, then shortest path, then stable by path
        return (not is_protected(r.path), len(r.path), r.path)

    decisions: list[DedupDecision] = []
    for sha1, recs in sorted(find_duplicate_groups(records).items()):
        by_parent: dict[str, list[DriveRecord]] = {}
        for r in recs:
            by_parent.setdefault(_parent(r.path), []).append(r)

        same_folder_delete: list[DriveRecord] = []
        reps: list[DriveRecord] = []
        for group in by_parent.values():
            ordered = sorted(group, key=rank)
            reps.append(ordered[0])
            same_folder_delete.extend(ordered[1:])

        if len(reps) == 1:
            decisions.append(DedupDecision(
                sha1, reps[0], tuple(same_folder_delete), (), "same-folder", "high"))
            continue

        protected = [r for r in reps if is_protected(r.path)]
        unprotected = [r for r in reps if not is_protected(r.path)]
        copies = [r for r in reps if is_copy(r.path)]
        noncopies = [r for r in reps if not is_copy(r.path)]
        tops = {_top(r.path) for r in reps}

        if protected and unprotected:
            keep = sorted(protected, key=rank)[0]
            delete = tuple(same_folder_delete) + tuple(unprotected)
            review = tuple(r for r in protected if r is not keep)
            decisions.append(DedupDecision(sha1, keep, delete, review, "protect", "high"))
        elif noncopies and copies:
            keep = sorted(noncopies, key=rank)[0]
            delete = tuple(same_folder_delete) + tuple(copies)
            review = tuple(r for r in noncopies if r is not keep)
            decisions.append(DedupDecision(sha1, keep, delete, review, "clear-copy", "high"))
        elif len(tops) == 1 and not protected:
            keep = sorted(reps, key=rank)[0]
            delete = tuple(same_folder_delete) + tuple(r for r in reps if r is not keep)
            decisions.append(DedupDecision(sha1, keep, delete, (), "intra-area", "medium"))
        else:
            keep = sorted(reps, key=rank)[0]
            review = tuple(r for r in reps if r is not keep)
            decisions.append(DedupDecision(
                sha1, keep, tuple(same_folder_delete), review, "review", "low"))
    return decisions


def summarize(decisions: Iterable[DedupDecision]) -> dict[str, dict[str, int]]:
    """Per reason: number of groups, files to delete, files to review, reclaimable bytes."""
    out: dict[str, dict[str, int]] = {}
    for d in decisions:
        s = out.setdefault(d.reason, {"groups": 0, "delete": 0, "review": 0, "bytes": 0})
        s["groups"] += 1
        s["delete"] += len(d.delete)
        s["review"] += len(d.review)
        s["bytes"] += d.reclaimable_bytes
    return out
