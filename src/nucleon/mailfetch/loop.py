"""The folder-wise, byte-bounded mail-fetch loop.

For each folder, items are planned into byte-bounded batches. Each message is fetched
(one at a time, so the working set is one message's bytes), its attachments decoded and
hashed, and each attachment checked for presence in the Drive:

  - ``present``  -> already in the Drive, nothing to stage;
  - ``absent``   -> staged into the Eingangsordner (via the injected stager);
  - ``unknown``  -> inconclusive; NOT staged, and the source message is kept.

A source message is trashed (reversible) ONLY when every one of its attachments is
accounted for (present or successfully staged) AND the run is in delete mode. A message
with an ``unknown`` attachment, a staging error, or no attachments at all is kept — the
original is never destroyed on an inconclusive or failed result.

All effects are injected (Fetcher/Stager/Deleter/PresenceLookup) so the loop is pure and
unit-testable; nothing here talks to IMAP, the Drive, or the disk directly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol

from nucleon.presence import Attachment, check_presence, PresenceLookup
from nucleon.mailfetch.batcher import Batch, Item, SkipOversize, plan_batches
from nucleon.mailfetch.budget import BudgetExceeded, DiskBudget
from nucleon.mailfetch.parse import AttachmentBlob, extract_with_payload


@dataclass(frozen=True)
class MsgRef:
    uid: str
    size: int


class Fetcher(Protocol):
    def list_messages(self, folder: str) -> list[MsgRef]: ...
    def fetch_raw(self, folder: str, uid: str) -> bytes: ...


class Stager(Protocol):
    # Stage one attachment into the Eingangsordner. Idempotent on sha1 (a file already
    # present under the same content need not be written twice). Returns the target path.
    def stage(self, filename: str, data: bytes, sha1: str) -> str: ...


class Deleter(Protocol):
    def trash(self, folder: str, uid: str) -> None: ...


@dataclass
class FolderReport:
    folder: str
    listed: int = 0
    processed: int = 0
    attachments: int = 0
    staged: int = 0
    already_present: int = 0
    kept_unknown: int = 0
    kept_no_attachments: int = 0
    deleted: int = 0
    skipped_oversize: int = 0
    errors: int = 0


def process_folder(
    folder: str,
    fetcher: Fetcher,
    lookup: PresenceLookup,
    stager: Stager,
    deleter: Optional[Deleter],
    budget: DiskBudget,
    *,
    under: Optional[str] = None,
    do_delete: bool = False,
) -> FolderReport:
    rep = FolderReport(folder=folder)
    refs = [Item(m.uid, m.size) for m in fetcher.list_messages(folder)]
    rep.listed = len(refs)
    for unit in plan_batches(refs, budget.cap_bytes):
        if isinstance(unit, SkipOversize):
            rep.skipped_oversize += 1
            continue
        assert isinstance(unit, Batch)
        for it in unit.items:
            _process_one(folder, it, fetcher, lookup, stager, deleter, budget, under, do_delete, rep)
    return rep


def _process_one(folder, it, fetcher, lookup, stager, deleter, budget, under, do_delete, rep):
    try:
        raw = fetcher.fetch_raw(folder, it.id)
    except Exception:
        rep.errors += 1
        return
    try:
        budget.acquire(len(raw))
    except BudgetExceeded:
        rep.skipped_oversize += 1  # couldn't hold it within the working set -> leave untouched
        return
    try:
        blobs = extract_with_payload(raw)
        rep.processed += 1
        rep.attachments += len(blobs)
        if not blobs:
            rep.kept_no_attachments += 1
            return
        atts = [Attachment(b.filename, b.sha1, b.size) for b in blobs]
        presences = check_presence(atts, lookup, under=under)
        all_accounted = True
        for blob, ap in zip(blobs, presences):
            if ap.status == "present":
                rep.already_present += 1
            elif ap.status == "absent":
                try:
                    stager.stage(blob.filename, blob.data, blob.sha1)
                    rep.staged += 1
                except Exception:
                    rep.errors += 1
                    all_accounted = False
            else:  # unknown
                all_accounted = False
        if all_accounted and do_delete and deleter is not None:
            deleter.trash(folder, it.id)
            rep.deleted += 1
        elif not all_accounted:
            rep.kept_unknown += 1
    finally:
        budget.release(len(raw))
