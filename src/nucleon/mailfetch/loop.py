"""The folder-wise, byte-bounded mail-fetch loop.

For each folder, items are planned into byte-bounded batches. Each message is fetched
(one at a time, so the working set is one message's bytes), its attachments decoded and
hashed, and each attachment checked for presence in the Drive:

  - ``present``  -> already in the Drive, nothing to stage;
  - ``absent``   -> staged into the Eingangsordner (via the injected stager);
  - ``unknown``  -> inconclusive; NOT staged, and the source message is kept.

A source message is trashed (reversible) ONLY when every one of its attachments is
accounted for (present or successfully staged), the run is in delete mode, AND a durable
Abdruck of the message has just been written. A message with an ``unknown`` attachment, a
staging error, a missing/failed Abdruck, or no attachments at all is kept — the original is
never destroyed on an inconclusive or failed result.

Two hardening contracts are enforced here at the executing boundary (not only in the
``pipeline`` layer):
  - **R-033 — Abdruck-before-delete:** ``do_delete`` with no ``recorder`` never deletes; a
    recorder that raises keeps the source. The guarantee does not depend on the caller.
  - **R-036 — budget before fetch:** the working-set budget is reserved by the *announced*
    size before the message is materialized, reconciled against the actual bytes, and each
    decoded attachment's bytes are charged while it is staged.

All effects are injected (Fetcher/Stager/Deleter/PresenceLookup/Recorder) so the loop is pure
and unit-testable; nothing here talks to IMAP, the Drive, or the disk directly.
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


class Recorder(Protocol):
    # Make a durable record (e.g. a PDF-Abdruck) of the raw message BEFORE it is trashed.
    # Returns an identifier/path for the record. Satisfied structurally by
    # ``pipeline.abdruck.Abdruck``. The loop is pure: the record itself is the recorder's job.
    def record(self, folder: str, uid: str, raw: bytes) -> str: ...


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
    kept_no_abdruck: int = 0   # all accounted + delete requested, but the Abdruck failed/absent
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
    recorder: Optional[Recorder] = None,
) -> FolderReport:
    rep = FolderReport(folder=folder)
    refs = [Item(m.uid, m.size) for m in fetcher.list_messages(folder)]
    rep.listed = len(refs)
    for unit in plan_batches(refs, budget.effective_cap):
        if isinstance(unit, SkipOversize):
            rep.skipped_oversize += 1
            continue
        assert isinstance(unit, Batch)
        for it in unit.items:
            _process_one(folder, it, fetcher, lookup, stager, deleter, budget,
                         under, do_delete, recorder, rep)
    return rep


def _process_one(folder, it, fetcher, lookup, stager, deleter, budget, under,
                 do_delete, recorder, rep):
    # R-036: reserve the working-set budget by the ANNOUNCED size BEFORE the message is
    # fetched, so an oversized transfer is refused before it is materialized in memory/disk.
    try:
        budget.acquire(it.size)
    except BudgetExceeded:
        rep.skipped_oversize += 1
        return
    acquired = it.size
    try:
        try:
            raw = fetcher.fetch_raw(folder, it.id)
        except Exception:
            rep.errors += 1
            return
        # R-036: reconcile the announced size against the real byte count on the same budget.
        if len(raw) > acquired:
            try:
                budget.acquire(len(raw) - acquired)
            except BudgetExceeded:
                rep.skipped_oversize += 1
                return
            acquired = len(raw)
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
                # R-036: the decoded attachment is extra working set while it is staged;
                # charge its bytes to the same budget, then release.
                try:
                    budget.acquire(blob.size)
                except BudgetExceeded:
                    rep.skipped_oversize += 1
                    all_accounted = False
                    continue
                try:
                    stager.stage(blob.filename, blob.data, blob.sha1)
                    rep.staged += 1
                except Exception:
                    rep.errors += 1
                    all_accounted = False
                finally:
                    budget.release(blob.size)
            else:  # unknown
                all_accounted = False
        if all_accounted and do_delete and deleter is not None:
            # R-033: deleting the original requires a durable Abdruck written FIRST, enforced
            # here at the executing boundary. No recorder -> never delete; a recorder that
            # raises -> keep the source. The guarantee does not rely on the caller.
            if recorder is None:
                rep.kept_no_abdruck += 1
            else:
                try:
                    recorder.record(folder, it.id, raw)
                except Exception:
                    rep.errors += 1
                    rep.kept_no_abdruck += 1
                else:
                    # A trash failure must not abort the folder; the Abdruck already exists and
                    # the source is simply kept (never lost).
                    try:
                        deleter.trash(folder, it.id)
                    except Exception:
                        rep.errors += 1
                    else:
                        rep.deleted += 1
        elif not all_accounted:
            rep.kept_unknown += 1
    except Exception:
        rep.errors += 1   # one malformed message (parse/presence) must not abort the folder
    finally:
        budget.release(acquired)
