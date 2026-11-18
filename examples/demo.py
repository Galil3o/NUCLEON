"""Offline Nucleon demo: the whole pipeline against in-memory fakes.

No network, no real mailbox, no credentials. Run: python examples/demo.py
"""
from __future__ import annotations

import email
import os
import sys
import tempfile
from email import policy

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from nucleon.config import Config  # noqa: E402
from nucleon.engine import reconcile, run_once  # noqa: E402
from nucleon.gate import decide  # noqa: E402
from nucleon.journal import FileJournalStore, StoredJournal, build_journal  # noqa: E402
from nucleon.marker import build_marker  # noqa: E402
from nucleon.mailstore.fake import FakeMailStore  # noqa: E402
from nucleon.models import (  # noqa: E402
    ApplyLabel, Classification, DeleteToTrash, MessageMeta, MessageRef, StageDraft,
)
from nucleon.quarantine import Classifier  # noqa: E402

ACCOUNT = "demo-account"


def make_meta(uid: int, subject: str, sender: str, mid: str, **kw) -> MessageMeta:
    ref = MessageRef.from_source(ACCOUNT, "INBOX", 1, uid, mid, f"raw-{uid}".encode())
    return MessageMeta(ref=ref, subject=subject, from_addr=sender,
                       to_addrs=["me@example.com"], message_id=mid, **kw)


class DemoClassifier:
    """Canned classifier keyed by subject (stands in for the local LLM)."""

    def classify(self, meta: MessageMeta, body_text: str) -> Classification:
        s = meta.subject.lower()
        if "lunch" in s:
            return Classification(
                category="personal", labels=["nucleon/personal", "nucleon/follow-up"], urgency="normal",
                needs_reply=True,
                draft_body="Hi,\n\nThursday at noon works for me. See you then!\n")
        if "newsletter" in s:
            return Classification(category="newsletter", labels=["nucleon/newsletter"],
                                  urgency="low", needs_reply=False)
        return Classification(category="notification", labels=["nucleon/notification"],
                              urgency="low", needs_reply=False)


assert isinstance(DemoClassifier(), Classifier)


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def main() -> None:
    print("Nucleon offline demo")
    print("This runs the full pipeline against in-memory fakes: no network, no real")
    print("mailbox, no credentials. Nothing leaves this process.")

    tmp = tempfile.mkdtemp(prefix="nucleon-demo-")
    journal_path = os.path.join(tmp, "journal.jsonl")
    cfg = Config.model_validate({
        "mail": {"id": "demo", "mode": "imap", "host": "bridge.invalid", "port": 1143,
                 "username": "me@example.com"},
        "classifier": {"endpoint": "http://127.0.0.1:1", "model": "demo"},
        "draft": {"target": "drafts", "label": "nucleon/proposed"},
        "cleanup": {"auto_trash_enabled": True, "auto_trash_categories": ["newsletter"]},
        "journal": {"enabled": True, "backend": "file", "path": journal_path},
        "marker": {"backend": "file", "path": os.path.join(tmp, "marker.jsonl")},
    })

    store = FakeMailStore()
    msgs = [
        (make_meta(1, "Lunch on Thursday?", "sender@example.com", "<lunch-1@example.com>"),
         "Hi, are you free for lunch this Thursday?"),
        (make_meta(2, "Weekly newsletter: top 10 tips", "news@example.com",
                   "<news-2@example.com>"), "Ten tips you will not believe. Unsubscribe below."),
        (make_meta(3, "Your build finished", "ci@example.com", "<ci-3@example.com>"),
         "Build #42 passed."),
    ]
    for meta, body in msgs:
        store.seed_inbox(meta, body)

    section("1. Seeded inbox")
    for meta, _ in msgs:
        print(f"  uid={meta.ref.uid}  from={meta.from_addr}  subject={meta.subject!r}")

    section("2. Classification and gate decisions (per message)")
    clf = DemoClassifier()
    for meta, body in msgs:
        c = clf.classify(meta, body)
        print(f"  [{meta.subject}]")
        print(f"    classification: category={c.category} urgency={c.urgency} "
              f"needs_reply={c.needs_reply} labels={c.labels}")
        for a in decide(c, meta, cfg):
            if isinstance(a, ApplyLabel):
                print(f"    gate -> ApplyLabel({a.label!r})")
            elif isinstance(a, StageDraft):
                print(f"    gate -> StageDraft(target={a.target!r}, to={a.to!r})")
            elif isinstance(a, DeleteToTrash):
                print("    gate -> DeleteToTrash (opt-in cleanup, reversible)")

    section("3. Engine run_once")
    journal = build_journal(cfg.journal)
    report = run_once(store, clf, journal, cfg, build_marker(cfg.marker))
    print("  (executing the gate's actions against the fake store)")

    section("4. What the fake store recorded")
    print("  Labels set:")
    for ref, label in store.labels:
        print(f"    uid={ref.uid} mailbox={ref.mailbox}: {label}")
    print("  Drafts appended:")
    for target, raw in store.appended_drafts:
        m = email.message_from_bytes(raw, policy=policy.default)
        print(f"    target={target}")
        print(f"      Subject:     {m['Subject']}")
        print(f"      To:          {m['To']}")
        print(f"      In-Reply-To: {m['In-Reply-To']}")
        print(f"      References:  {m['References']}")
        print("      Body: " + m.get_content().strip().replace("\n", " "))
        print(f"      (draft label {cfg.draft.label!r} applied to the draft)")
    print("  Moved to Trash:")
    for ref in store.moved_to_trash:
        print(f"    uid={ref.uid} (category newsletter, cleanup opted in)")

    section("5. RunReport")
    for field in ("processed", "skipped", "labeled", "drafted", "trashed", "errors"):
        print(f"  {field:10s} {getattr(report, field)}")

    section("6. Journal entries (metadata and hashes only, no content)")
    for e in StoredJournal(FileJournalStore(journal_path))._store.entries():
        print(f"  uid={e.uid} action={e.action} status={e.status} "
              f"message_id_hash={(e.message_id_hash or '')[:12]}... "
              f"raw_hash={e.raw_hash[:12]}...")

    section("7. Reconciliation")
    print(f"  Unsent drafts before: {len(journal.drafted_unsent())}")
    sent_ref = MessageRef.from_source(ACCOUNT, "Sent", 1, 1, "<sent-1@example.com>", b"sent-1")
    store.seed_sent(MessageMeta(
        ref=sent_ref, subject="Re: Lunch on Thursday?", from_addr="me@example.com",
        to_addrs=["sender@example.com"], message_id="<sent-1@example.com>",
        in_reply_to="<lunch-1@example.com>"))
    print("  Seeded Sent folder with a reply whose In-Reply-To is <lunch-1@example.com>")
    n = reconcile(store, journal, cfg)
    print(f"  Drafts marked sent: {n}")
    print(f"  Unsent drafts after: {len(journal.drafted_unsent())}")

    print("\nNote: a live run needs a real Proton Bridge (IMAP) and a local classifier")
    print("endpoint; that is deferred. This demo only exercises the offline pipeline.")


if __name__ == "__main__":
    main()
