"""Dedup planning over DriveRecords.

Turns duplicate groups (files sharing the same content sha1 AND the same known size)
into keep / delete / review decisions under configurable rules. Pure and read-only:
it produces a plan and touches no Drive.

Safety design (this plan drives deletions on an irreplaceable archive):
  - **Identity gate:** files count as duplicates only when sha1 AND size match and are
    known. A sha1 is a *client-claimed* digest (``claimedDigests.sha1``, usually
    unverified); requiring equal size guards against a wrong/collided hash conflating
    two different files. Records without a size, or a sha1 whose group has differing
    sizes, are never dedup candidates.
  - **Input de-duplication:** records repeated in the listing (same uid+path) are
    collapsed, and no record sharing the kept record's uid is ever proposed for
    deletion — so an executor deleting by uid cannot remove the file we keep.
  - **Protected areas are never auto-deleted:** any deletion candidate under a
    ``protect_prefix`` (e.g. "/CASES") is moved to ``review`` instead of ``delete``.
    Protected content only ever surfaces for human review.
  - **Conservative copy heuristic:** only fairly unambiguous markers (Kopie/Copy/
    .tmp/VF-DROP/autorecover) count as "copy" locations. Ambiguous terms like
    "Archiv"/"Versand" are excluded (they appear in real case/party folder names);
    add your own copy-dump folder names via ``copy_patterns`` if you want them.

Reasons (first match per group of distinct folders), then protected-safety filter:
  same-folder (high) | protect (high) | clear-copy (medium) | intra-area (medium)
  | review (low).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from proton_drive_kit.records import DriveRecord

DEFAULT_COPY_PATTERNS: tuple[str, ...] = (
    r"\bkopie\b", r"\bcopy\b", r"copy_of", r"\.tmp$", r"autorecover", r"vf-?drop",
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


def _unique(records: Iterable[DriveRecord]) -> list[DriveRecord]:
    """Collapse records repeated in the listing (same uid+path)."""
    seen: set[tuple[str, str]] = set()
    out: list[DriveRecord] = []
    for r in records:
        key = (r.uid, r.path)
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def find_duplicate_groups(records: Iterable[DriveRecord]) -> dict[str, list[DriveRecord]]:
    """Map content sha1 -> records sharing it, only for safe groups: 2+ files, all with
    the SAME known size (identity gate). Folders and records without a sha1 are ignored;
    a sha1 whose records disagree on size, or have an unknown size, is dropped."""
    groups: dict[str, list[DriveRecord]] = {}
    for r in _unique(records):
        if r.is_folder or not r.sha1:
            continue
        groups.setdefault(r.sha1, []).append(r)
    out: dict[str, list[DriveRecord]] = {}
    for sha1, recs in groups.items():
        if len(recs) < 2:
            continue
        sizes = {r.size for r in recs}
        if None in sizes or len(sizes) != 1:
            continue  # unknown or conflicting size -> not a safe duplicate
        (only_size,) = tuple(sizes)
        if only_size <= 0:
            # Every empty file shares the empty-file sha1 AND size 0; grouping them would
            # conflate unrelated files and reclaim nothing. Not a safe content duplicate.
            continue
        out[sha1] = recs
    return out


def plan_dedup(
    records: Iterable[DriveRecord],
    *,
    protect_prefixes: tuple[str, ...] = (),
    copy_patterns: tuple[str, ...] = DEFAULT_COPY_PATTERNS,
) -> list[DedupDecision]:
    records = list(records)  # materialized: scanned twice (digest grouping + protected-uid pass)
    copy_res = [re.compile(p, re.IGNORECASE) for p in copy_patterns if p]
    prefixes = tuple("/" + p.strip("/") for p in protect_prefixes if p.strip("/"))
    folded_prefixes = tuple(pre.casefold() for pre in prefixes)

    def is_copy(path: str) -> bool:
        segs = path.strip("/").split("/")
        return any(cr.search(seg) for seg in segs for cr in copy_res)

    def is_protected(path: str) -> bool:
        # Case-insensitive: folder-name casing must never defeat a protect prefix on an
        # irreplaceable archive. Over-protection (more to review, fewer deletes) is the
        # safe direction. Segment boundary is still honoured ('/casesx' is not '/cases').
        p = path.casefold()
        return any(p == pre or p.startswith(pre + "/") for pre in folded_prefixes)

    def rank(r: DriveRecord) -> tuple:
        return (not is_protected(r.path), len(r.path), r.path)

    # Protected uids are determined ONCE over ALL input paths, BEFORE any digest grouping:
    # a uid whose protected path carries a different/absent digest would otherwise drop out
    # of the duplicate group and stay deletable via its in-group path.
    all_protected_uids = {r.uid for r in records if r.uid and is_protected(r.path)}

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
            keep = reps[0]
            delete = list(same_folder_delete)
            review: list[DriveRecord] = []
            reason, confidence = "same-folder", "high"
        else:
            protected = [r for r in reps if is_protected(r.path)]
            unprotected = [r for r in reps if not is_protected(r.path)]
            copies = [r for r in reps if is_copy(r.path)]
            noncopies = [r for r in reps if not is_copy(r.path)]
            tops = {_top(r.path) for r in reps}

            if protected and unprotected:
                keep = sorted(protected, key=rank)[0]
                delete = list(same_folder_delete) + list(unprotected)
                review = [r for r in protected if r is not keep]
                reason, confidence = "protect", "high"
            elif noncopies and copies:
                keep = sorted(noncopies, key=rank)[0]
                delete = list(same_folder_delete) + list(copies)
                review = [r for r in noncopies if r is not keep]
                reason, confidence = "clear-copy", "medium"
            elif len(tops) == 1 and not protected:
                keep = sorted(reps, key=rank)[0]
                delete = list(same_folder_delete) + [r for r in reps if r is not keep]
                review = []
                reason, confidence = "intra-area", "medium"
            else:
                keep = sorted(reps, key=rank)[0]
                delete = list(same_folder_delete)
                review = [r for r in reps if r is not keep]
                reason, confidence = "review", "low"

        # Protected-safety: never auto-delete anything protected -> move to review. A uid is
        # protected if ANY of its INPUT paths is protected (all_protected_uids, computed once
        # over all records before digest grouping): a shared node under a protect prefix must
        # not become deletable via another path — even if its protected path has a different
        # or absent digest and so is not in this duplicate group.
        def _protected(r):
            return is_protected(r.path) or (r.uid and r.uid in all_protected_uids)

        moved = [r for r in delete if _protected(r)]
        delete = [r for r in delete if not _protected(r)]
        review = review + moved

        # Executor-by-uid safety. An executor deletes by uid, so:
        #  - without a positively-identified keep (a uid), nothing can be distinguished from
        #    the file to preserve -> propose NO deletions at all;
        #  - a record sharing the kept file's uid is the kept file under a repeated node ->
        #    drop it from delete AND review;
        #  - a delete candidate with no stable uid cannot be targeted without risking the
        #    wrong file -> surface it for review instead of auto-deleting.
        if not keep.uid:
            review = review + delete
            delete = []
        else:
            delete = [r for r in delete if r.uid != keep.uid]
            review = [r for r in review if r.uid != keep.uid]
            uidless = [r for r in delete if not r.uid]
            if uidless:
                delete = [r for r in delete if r.uid]
                review = review + uidless

        if not delete and review:
            reason, confidence = "review", "low"

        decisions.append(DedupDecision(
            sha1, keep, tuple(delete), tuple(review), reason, confidence))
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
