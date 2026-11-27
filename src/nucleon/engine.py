"""One engine pass (classify -> gate -> execute -> journal) and reconciliation."""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from typing import Literal

from nucleon import actions as actions_mod
import logging

from nucleon.analysis import AnalysisLog, pseudonym
from nucleon.budgets import BudgetEvent, BudgetExceeded, cap_actions
from nucleon.accounts import account_hash_for
from nucleon.actions import draft_message_id
from nucleon import gate
from nucleon.config import Config
from nucleon.journal import Journal
from nucleon.marker import ProcessingMarker
from nucleon.mailstore import MailStore, PreflightError, PreflightRequest, StaleReferenceError
from nucleon.models import ApplyLabel, DeleteToTrash, DraftRef, JournalEntry, StageDraft
from nucleon.quarantine import Classifier
from nucleon.recipients import ContactBook


@dataclass(frozen=True)
class RunReport:
    processed: int = 0
    skipped: int = 0
    labeled: int = 0
    drafted: int = 0
    trashed: int = 0
    errors: int = 0
    vanished: int = 0
    # R-011: messages rejected by a hard size budget, and every visible overrun
    # (rejected or truncated) per message. Metadata only.
    over_budget: int = 0
    budget_events: tuple[BudgetEvent, ...] = ()


_log = logging.getLogger("nucleon.budget")


def run_once(
    store: MailStore,
    classifier: Classifier,
    journal: Journal,
    cfg: Config,
    marker: ProcessingMarker,
    analysis: AnalysisLog | None = None,
    contacts: ContactBook | None = None,
) -> RunReport:
    # R-003: a write run without an effective marker would duplicate drafts on
    # every repeat, so it is refused outright (the audit journal is optional).
    if marker is None:
        raise ValueError("run_once requires a processing marker")
    processed = skipped = labeled = drafted = trashed = errors = vanished = 0
    over_budget = 0
    events: list[BudgetEvent] = []
    lim = cfg.limits

    def _event(ref, kind, outcome, limit, observed) -> None:
        ev = BudgetEvent(kind=kind, outcome=outcome, limit=limit, observed=observed,
                         ref_pseudonym=pseudonym(ref.processing_key()))
        events.append(ev)
        _log.warning("budget %s %s limit=%d observed=%d ref=%s", ev.kind, ev.outcome,
                     ev.limit, ev.observed, ev.ref_pseudonym)  # metadata only
    # R-005: the limit bounds the WORK left after the marker/journal filter,
    # not the window, so older unprocessed mail cannot starve behind newer
    # mail. Oldest-first iteration makes progress strictly monotonic.
    limit = cfg.mail.limit
    attempted = 0
    checked_folders: set[str] = set()  # R-010: preflight state for this run
    checked = {"draft": False, "move": False}
    for ref in store.iter_window(cfg.mail.mailbox, cfg.mail.since_days):
        pkey = ref.processing_key()
        if marker.is_done(pkey) or journal.has_processed(ref.key()):
            skipped += 1
            continue
        if attempted >= limit:
            break
        attempted += 1
        stage = "fetch"
        t0 = time.monotonic()
        steps: list[str] = []
        # Conservative policy: errors are surfaced, not auto-retried. A failing
        # message is journaled with status="error" (marking it processed, so a
        # retry cannot re-stage a duplicate draft) and the pass moves on. The
        # operator sees it via the journal and RunReport.errors. The exception
        # text is deliberately NOT recorded (it may carry untrusted content).
        try:
            meta = store.fetch_meta(ref)
            fetched = store.fetch_body(ref)  # size/structure checked before loading
            body = fetched.text
            input_truncated = fetched.truncated
            if fetched.truncated:
                _event(ref, "body_bytes", "truncated", lim.max_body_bytes,
                       fetched.original_bytes or 0)
            if len(body) > lim.max_model_input_chars:
                _event(ref, "model_input", "truncated", lim.max_model_input_chars, len(body))
                body = body[: lim.max_model_input_chars]
                input_truncated = True
            stage = "classify"
            classification = classifier.classify(meta, body)
            stage = "gate"
            acts = gate.decide(classification, meta, cfg, store.system_folders(),
                               contacts=contacts, input_truncated=input_truncated)
            acts, over = cap_actions(acts, lim.max_labels_per_message,
                                     lim.max_actions_per_message)
            for kind, limit, observed in over:
                _event(ref, kind, "truncated", limit, observed)
            stage = "act"
            did_draft = did_trash = False
            n_labeled = 0
            draft: list[DraftRef] = []  # latest stable draft reference (R-004)
            if acts:
                # R-010: folders / namespace / capabilities are verified before
                # the FIRST write of each kind; a failure aborts the whole run.
                _preflight(store, cfg, acts, checked, checked_folders)
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
        except PreflightError:
            raise  # run-level: nothing was written, the message stays retryable
        except StaleReferenceError:
            # R-002: UIDVALIDITY changed; the UID may now be a different
            # message. Treat as vanished: no further action, nothing recorded.
            vanished += 1
            continue
        except BudgetExceeded as be:
            # R-011: this message is rejected visibly; no retry, run continues.
            marker.mark(pkey, "error", tuple(steps))
            _record(journal, ref, "classified", "error")
            _event(ref, be.kind, "rejected", be.limit, be.observed)
            over_budget += 1
            continue
        except Exception as exc:
            # Completed sub-steps and the draft reference stay in the marker
            # (R-004); only the status changes. No automatic retry.
            marker.mark(pkey, "error", tuple(steps))
            _record(journal, ref, "classified", "error")
            _stage2(analysis, cfg, stage, exc, t0, ref, steps)
            errors += 1
            continue
        labeled += n_labeled
        drafted += did_draft
        trashed += did_trash
        processed += 1
    return RunReport(processed, skipped, labeled, drafted, trashed, errors, vanished,
                     over_budget, tuple(events))


def _preflight(store, cfg, acts, checked: dict, checked_folders: set) -> None:
    wants_draft = any(isinstance(a, StageDraft) for a in acts)
    wants_move = any(isinstance(a, DeleteToTrash) for a in acts)
    folders: list[str] = []
    for a in acts:
        name = a.label if isinstance(a, (ApplyLabel, StageDraft)) else None
        if name and name not in checked_folders and name not in folders:
            folders.append(name)
    need_draft = wants_draft and not checked["draft"]
    need_move = wants_move and not checked["move"]
    if not (need_draft or need_move or folders):
        return
    report = store.preflight(PreflightRequest(
        draft_targets=(cfg.draft.target,) if need_draft else (),
        folders=tuple(folders),
        need_move=need_move,
        create_missing=cfg.draft.create_missing_folders,
    ))
    for w in report.warnings:
        _log.warning("preflight: %s", w)
    if not report.ok:
        raise PreflightError("preflight failed: " + "; ".join(report.problems))
    checked["draft"] = checked["draft"] or wants_draft
    checked["move"] = checked["move"] or wants_move
    checked_folders.update(folders)


def _stage2(analysis, cfg, stage, exc, t0, ref, steps) -> None:
    """Stage 2 (R-008): fixed metadata record only. Never raises into the run."""
    if analysis is None or not cfg.analysis.enabled:
        return
    try:
        analysis.record_stage2(
            failed_stage=stage, exc=exc, duration_ms=int((time.monotonic() - t0) * 1000),
            ref_key=ref.processing_key(), account_hash=ref.account_hash, steps=tuple(steps),
        )
    except Exception:
        pass  # diagnostics must never break (or leak through) the pass


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


@dataclass(frozen=True)
class ReconcileReport:
    sent: int = 0  # provider-confirmed draft identity seen in Sent
    observed: int = 0  # only the conversation is evidenced: "observed reply"


def reconcile(store: MailStore, journal: Journal, cfg: Config) -> ReconcileReport:
    """Match Sent mail against open drafts. Read-only on the mailbox.

    "sent" requires the stable, provider-confirmed draft identity (the draft's
    own Message-ID, recorded with a confirmed APPENDUID) to appear in Sent. A
    reply that merely references the original message (In-Reply-To) proves only
    the conversation, so it is recorded as "observed reply" and the proposal
    stays open. Entries without a confirmed draft id can only ever be observed.
    """
    # R-012: only this account's drafts (bound to the stable id, not the login).
    mine = account_hash_for(cfg)
    pending = [e for e in journal.drafted_unsent() if e.account_hash == mine]
    by_draft = {e.draft_message_id_hash: e for e in pending
                if e.draft_message_id_hash and e.draft_confirmed}
    by_source: dict[str, list] = {}
    for e in pending:
        if e.message_id_hash:
            by_source.setdefault(e.message_id_hash, []).append(e)
    sent_n = observed_n = 0
    done: set[str] = set()
    sent_metas = store.list_sent(cfg.mail.since_days, cfg.mail.limit)
    for sent in sent_metas:  # pass 1: exact identity
        h = sent.ref.message_id_hash
        entry = by_draft.get(h) if h else None
        if entry is not None and entry.ref_key not in done:
            journal.mark_sent(entry.ref_key)
            done.add(entry.ref_key)
            sent_n += 1
    for sent in sent_metas:  # pass 2: conversation evidence only
        if sent.in_reply_to is None:
            continue
        h = hashlib.sha256(sent.in_reply_to.encode()).hexdigest()
        for entry in by_source.get(h, []):
            if entry.ref_key not in done and journal.mark_observed(entry.ref_key):
                observed_n += 1
    return ReconcileReport(sent_n, observed_n)
