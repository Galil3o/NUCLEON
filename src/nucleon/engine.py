"""One engine pass (classify -> gate -> execute -> journal) and reconciliation."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone

from nucleon import actions as actions_mod
from nucleon import gate
from nucleon.config import Config
from nucleon.journal import Journal
from nucleon.mailstore import MailStore
from nucleon.models import ApplyLabel, DeleteToTrash, JournalEntry, StageDraft
from nucleon.quarantine import Classifier


@dataclass(frozen=True)
class RunReport:
    processed: int = 0
    skipped: int = 0
    labeled: int = 0
    drafted: int = 0
    trashed: int = 0


def run_once(
    store: MailStore, classifier: Classifier, journal: Journal, cfg: Config
) -> RunReport:
    processed = skipped = labeled = drafted = trashed = 0
    refs = store.list_window(cfg.mail.mailbox, cfg.mail.since_days, cfg.mail.limit)
    for ref in refs:
        if journal.has_processed(ref.key()):
            skipped += 1
            continue
        meta = store.fetch_meta(ref)
        body = store.fetch_body_text(ref)
        classification = classifier.classify(meta, body)
        acts = gate.decide(classification, meta, cfg)
        did_draft = did_trash = False
        for a in acts:
            actions_mod.execute(a, store)
            if isinstance(a, ApplyLabel):
                labeled += 1
            elif isinstance(a, StageDraft):
                did_draft = True
                drafted += 1
            elif isinstance(a, DeleteToTrash):
                did_trash = True
                trashed += 1
        action = "drafted" if did_draft else "trashed" if did_trash else "classified"
        journal.record(
            JournalEntry(
                ref_key=ref.key(),
                account_hash=ref.account_hash,
                mailbox=ref.mailbox,
                uid=ref.uid,
                message_id_hash=ref.message_id_hash,
                raw_hash=ref.raw_hash,
                action=action,
                status="ok",
                ts=datetime.now(timezone.utc),
            )
        )
        processed += 1
    return RunReport(processed, skipped, labeled, drafted, trashed)


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
