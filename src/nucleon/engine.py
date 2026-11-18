"""One engine pass (classify -> gate -> execute -> journal) and reconciliation."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone

from typing import Literal

from nucleon import actions as actions_mod
from nucleon.actions import draft_message_id
from nucleon import gate
from nucleon.config import Config
from nucleon.journal import Journal
from nucleon.marker import ProcessingMarker
from nucleon.mailstore import MailStore, StaleReferenceError
from nucleon.models import ApplyLabel, DeleteToTrash, DraftRef, JournalEntry, StageDraft
from nucleon.quarantine import Classifier


@dataclass(frozen=True)
class RunReport:
    processed: int = 0
    skipped: int = 0
    labeled: int = 0
    drafted: int = 0
    trashed: int = 0
    errors: int = 0
    vanished: int = 0


def run_once(
    store: MailStore,
    classifier: Classifier,
    journal: Journal,
    cfg: Config,
    marker: ProcessingMarker,
) -> RunReport:
    # R-003: a write run without an effective marker would duplicate drafts on
    # every repeat, so it is refused outright (the audit journal is optional).
    if marker is None:
        raise ValueError("run_once requires a processing marker")
    processed = skipped = labeled = drafted = trashed = errors = vanished = 0
    # R-005: the limit bounds the WORK left after the marker/journal filter,
    # not the window, so older unprocessed mail cannot starve behind newer
    # mail. Oldest-first iteration makes progress strictly monotonic.
    limit = cfg.mail.limit
    attempted = 0
    for ref in store.iter_window(cfg.mail.mailbox, cfg.mail.since_days):
        pkey = ref.processing_key()
        if marker.is_done(pkey) or journal.has_processed(ref.key()):
            skipped += 1
            continue
        if attempted >= limit:
            break
        attempted += 1
        # Conservative policy: errors are surfaced, not auto-retried. A failing
        # message is journaled with status="error" (marking it processed, so a
        # retry cannot re-stage a duplicate draft) and the pass moves on. The
        # operator sees it via the journal and RunReport.errors. The exception
        # text is deliberately NOT recorded (it may carry untrusted content).
        try:
            meta = store.fetch_meta(ref)
            body = store.fetch_body_text(ref)
            classification = classifier.classify(meta, body)
            acts = gate.decide(classification, meta, cfg, store.system_folders())
            did_draft = did_trash = False
            n_labeled = 0
            steps: list[str] = []
            draft: list[DraftRef] = []  # latest stable draft reference (R-004)
            if acts:
                # Durable BEFORE the first mailbox write: a crash mid-action
                # then cannot cause a duplicate draft/label on the next run.
                marker.mark(pkey, "started")

            def _intent(d: DraftRef) -> None:
                # Before the APPEND: if its outcome turns out unclear, the
                # reference needed to look the draft up is already durable.
                draft[:] = [d]
                marker.mark(pkey, "started", tuple(steps), d)

            def _appended(d: DraftRef) -> None:
                # Right after the APPEND, before the draft label.
                draft[:] = [d]
                _add(steps, "StageDraft:appended")
                marker.mark(pkey, "started", tuple(steps), d)

            for a in acts:
                actions_mod.execute(
                    a, store, on_draft_intent=_intent, on_draft_appended=_appended
                )
                step = type(a).__name__
                if _add(steps, step):
                    marker.mark(pkey, "started", tuple(steps))  # sub-step is durable now
                if isinstance(a, ApplyLabel):
                    n_labeled += 1
                elif isinstance(a, StageDraft):
                    did_draft = True
                elif isinstance(a, DeleteToTrash):
                    did_trash = True
            action = "drafted" if did_draft else "trashed" if did_trash else "classified"
            marker.mark(pkey, "ok", tuple(steps))
            _record(journal, ref, action, "ok", draft[0] if draft else None)
        except StaleReferenceError:
            # R-002: UIDVALIDITY changed; the UID may now be a different
            # message. Treat as vanished: no further action, nothing recorded.
            vanished += 1
            continue
        except Exception:
            # Completed sub-steps and the draft reference stay in the marker
            # (R-004); only the status changes. No automatic retry.
            marker.mark(pkey, "error", tuple(steps))
            _record(journal, ref, "classified", "error")
            errors += 1
            continue
        labeled += n_labeled
        drafted += did_draft
        trashed += did_trash
        processed += 1
    return RunReport(processed, skipped, labeled, drafted, trashed, errors, vanished)


def _add(steps: list[str], step: str) -> bool:
    if step in steps:
        return False
    steps.append(step)
    return True


def _record(journal: Journal, ref, action: str, status: str, draft: DraftRef | None = None) -> None:
    journal.record(
        JournalEntry(
            ref_key=ref.key(),
            account_hash=ref.account_hash,
            mailbox=ref.mailbox,
            uid=ref.uid,
            message_id_hash=ref.message_id_hash,
            raw_hash=ref.raw_hash,
            action=action,
            status=status,
            ts=datetime.now(timezone.utc),
            draft_message_id_hash=draft.message_id_hash if draft else None,
            draft_confirmed=draft.confirmed if draft else False,
        )
    )


@dataclass(frozen=True)
class PartialFinding:
    key: str  # processing key of the source message (hashes only)
    status: str  # marker status: "started" (interrupted) or "error"
    steps: tuple[str, ...]
    # "none": no draft was attempted; "present"/"absent": the read-only lookup
    # found / did not find it; "unknown": the lookup itself failed.
    draft_state: Literal["none", "present", "absent", "unknown"]


def diagnose_partial(store: MailStore, marker: ProcessingMarker) -> list[PartialFinding]:
    """Reconcile unclear/partial outcomes BEFORE anyone considers a retry (R-004).

    STRICTLY READ-ONLY with respect to mail and calendar: the only store call
    is ``find_draft`` (EXAMINE/SEARCH/PEEK). It never appends, labels, moves or
    retries anything, and does not even write the marker. The retry policy for
    partial runs is an owner decision; the safe default is no auto-retry.
    """
    out: list[PartialFinding] = []
    for e in marker.unfinished():
        state: Literal["none", "present", "absent", "unknown"] = "none"
        if e.draft is not None:
            try:
                found = store.find_draft(e.draft.target, draft_message_id(e.key))
                state = "present" if found else "absent"
            except Exception:
                state = "unknown"
        out.append(PartialFinding(e.key, e.status, e.steps, state))
    return out


def reconcile(store: MailStore, journal: Journal, cfg: Config) -> int:
    """Mark drafted entries as sent when a Sent reply to the original appears.

    Read-only on the mailbox: only ``list_sent`` is called.
    """
    pending = {e.message_id_hash: e for e in journal.drafted_unsent() if e.message_id_hash}
    count = 0
    for sent in store.list_sent(cfg.mail.since_days, cfg.mail.limit):
        if sent.in_reply_to is None:
            continue
        h = hashlib.sha256(sent.in_reply_to.encode()).hexdigest()
        entry = pending.pop(h, None)
        if entry is not None:
            journal.mark_sent(entry.ref_key)
            count += 1
    return count
