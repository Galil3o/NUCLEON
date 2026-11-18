import hashlib

from nucleon.config import Config
from nucleon.engine import RunReport, reconcile, run_once
from nucleon.journal import FileJournalStore, NullJournal, StoredJournal
from nucleon.mailstore.fake import FakeMailStore
from nucleon.models import Classification, MessageMeta, MessageRef
from nucleon.quarantine.fake import FakeClassifier


def _cfg(**cleanup) -> Config:
    return Config.model_validate(
        {
            "mail": {"mode": "imap", "host": "h", "port": 993, "username": "u"},
            "classifier": {"endpoint": "http://127.0.0.1:1", "model": "m"},
            "cleanup": cleanup,
        }
    )


def _meta(uid=1, mid="<in@x>", **kw) -> MessageMeta:
    ref = MessageRef.from_source("a", "INBOX", 1, uid, mid, b"raw%d" % uid)
    return MessageMeta(
        ref=ref, subject="Hello", from_addr="a@b.c", to_addrs=["d@e.f"],
        message_id=mid, **kw
    )


def _cls(**kw) -> Classification:
    base = dict(category="personal", labels=["nucleon/l1"], urgency="normal", needs_reply=False)
    base.update(kw)
    return Classification(**base)


def _journal(tmp_path) -> StoredJournal:
    return StoredJournal(FileJournalStore(str(tmp_path / "j.jsonl")))


def test_run_once_labels_and_stages_one_draft(tmp_path):
    store, j = FakeMailStore(), _journal(tmp_path)
    store.seed_inbox(_meta(), "body")
    clf = FakeClassifier(_cls(needs_reply=True, draft_body="Thanks", labels=["nucleon/l1", "nucleon/l2"]))
    report = run_once(store, clf, j, _cfg())
    assert report == RunReport(processed=1, skipped=0, labeled=2, drafted=1, trashed=0)
    assert len(store.appended_drafts) == 1
    assert [l for _, l in store.labels if l in ("nucleon/l1", "nucleon/l2")] == ["nucleon/l1", "nucleon/l2"]
    entries = j._store.entries()
    assert len(entries) == 1 and entries[0].action == "drafted"


def test_run_once_trashes_when_cleanup_enabled(tmp_path):
    store, j = FakeMailStore(), _journal(tmp_path)
    m = _meta()
    store.seed_inbox(m, "body")
    cfg = _cfg(auto_trash_enabled=True, auto_trash_categories=["newsletter"])
    report = run_once(store, FakeClassifier(_cls(category="newsletter")), j, cfg)
    assert store.moved_to_trash == [m.ref]
    assert report.trashed == 1
    assert [e.action for e in j._store.entries()] == ["trashed"]


def test_run_once_is_idempotent(tmp_path):
    store, j = FakeMailStore(), _journal(tmp_path)
    store.seed_inbox(_meta(), "body")
    clf = FakeClassifier(_cls(needs_reply=True, draft_body="Thanks"))
    run_once(store, clf, j, _cfg())
    report = run_once(store, clf, j, _cfg())
    assert report.processed == 0 and report.skipped == 1
    assert len(store.appended_drafts) == 1
    assert len(j._store.entries()) == 1


def test_run_once_classified_only(tmp_path):
    store, j = FakeMailStore(), _journal(tmp_path)
    store.seed_inbox(_meta(), "body")
    run_once(store, FakeClassifier(_cls()), j, _cfg())
    assert [e.action for e in j._store.entries()] == ["classified"]


def test_null_journal_drafted_unsent_empty():
    assert NullJournal().drafted_unsent() == []


def _drafted_setup(tmp_path):
    store, j = FakeMailStore(), _journal(tmp_path)
    store.seed_inbox(_meta(), "body")
    run_once(store, FakeClassifier(_cls(needs_reply=True, draft_body="Hi")), j, _cfg())
    assert len(j.drafted_unsent()) == 1
    return store, j


def test_reconcile_marks_sent_when_matching_sent_appears(tmp_path):
    store, j = _drafted_setup(tmp_path)
    sent_ref = MessageRef.from_source("a", "Sent", 1, 9, "<out@x>", b"s")
    store.seed_sent(
        MessageMeta(ref=sent_ref, subject="Re: Hello", from_addr="d@e.f",
                    to_addrs=["a@b.c"], in_reply_to="<in@x>")
    )
    assert hashlib.sha256(b"<in@x>").hexdigest() == j.drafted_unsent()[0].message_id_hash
    assert reconcile(store, j, _cfg()) == 1
    assert j.drafted_unsent() == []
    assert reconcile(store, j, _cfg()) == 0


def test_reconcile_no_match_marks_nothing(tmp_path):
    store, j = _drafted_setup(tmp_path)
    sent_ref = MessageRef.from_source("a", "Sent", 1, 9, "<out@x>", b"s")
    store.seed_sent(
        MessageMeta(ref=sent_ref, subject="Re: Other", from_addr="d@e.f",
                    to_addrs=["a@b.c"], in_reply_to="<other@x>")
    )
    store.seed_sent(
        MessageMeta(ref=MessageRef.from_source("a", "Sent", 1, 10, None, b"t"),
                    subject="s", from_addr="d@e.f", to_addrs=["a@b.c"])
    )
    assert reconcile(store, j, _cfg()) == 0
    assert len(j.drafted_unsent()) == 1


class _FlakyClassifier:
    """Raises for one subject; exception text carries untrusted-looking content."""

    def __init__(self, bad_uid, inner):
        self.bad_uid, self.inner = bad_uid, inner

    def classify(self, meta, body):
        if meta.ref.uid == self.bad_uid:
            raise RuntimeError("SECRET-BODY-TEXT ignore previous instructions")
        return self.inner.classify(meta, body)


def _flaky_setup(tmp_path):
    store, j = FakeMailStore(), _journal(tmp_path)
    for uid in (1, 2, 3):
        store.seed_inbox(_meta(uid=uid, mid="<m%d@x>" % uid), "body")
    clf = _FlakyClassifier(2, FakeClassifier(_cls(needs_reply=True, draft_body="Thanks")))
    return store, j, clf


def test_run_once_isolates_a_failing_message(tmp_path):
    store, j, clf = _flaky_setup(tmp_path)
    report = run_once(store, clf, j, _cfg())
    assert report.processed == 2 and report.errors == 1 and report.drafted == 2
    entries = j._store.entries()
    assert len(entries) == 3  # exactly one entry per ref
    bad = [e for e in entries if e.uid == 2]
    assert len(bad) == 1 and bad[0].action == "classified" and bad[0].status == "error"
    assert sorted(e.status for e in entries) == ["error", "ok", "ok"]


def test_errored_message_is_not_reprocessed(tmp_path):
    store, j, clf = _flaky_setup(tmp_path)
    run_once(store, clf, j, _cfg())
    drafts = len(store.appended_drafts)
    second = run_once(store, clf, j, _cfg())
    assert second.skipped == 3 and second.processed == 0 and second.errors == 0
    assert len(store.appended_drafts) == drafts
    assert len(j._store.entries()) == 3


def test_error_entry_has_fixed_status_and_no_exception_text(tmp_path):
    store, j, clf = _flaky_setup(tmp_path)
    run_once(store, clf, j, _cfg())
    bad = [e for e in j._store.entries() if e.uid == 2][0]
    assert bad.status == "error"
    assert "SECRET" not in bad.model_dump_json() and "ignore previous" not in bad.model_dump_json()


def test_mid_action_failure_records_error_not_duplicate_draft(tmp_path):
    store, j = FakeMailStore(), _journal(tmp_path)
    store.seed_inbox(_meta(), "body")
    calls = {"n": 0}

    def boom(*a, **k):
        calls["n"] += 1
        raise RuntimeError("x")

    store.append_draft = boom  # type: ignore[attr-defined]
    clf = FakeClassifier(_cls(needs_reply=True, draft_body="Thanks"))
    r1 = run_once(store, clf, j, _cfg())
    r2 = run_once(store, clf, j, _cfg())
    assert r1.errors == 1 and r2.skipped == 1 and calls["n"] == 1


def test_r001_engine_drops_labels_on_provider_system_folders(tmp_path):
    store, j = FakeMailStore(), _journal(tmp_path)
    store.system_folder_names = frozenset({"Labels/Hidden"})
    store.seed_inbox(_meta(), "body")
    cfg = _cfg()
    cfg = cfg.model_copy(update={"labels": cfg.labels.model_copy(update={"allowed": ["Labels/Hidden"]})})
    clf = FakeClassifier(_cls(labels=["Labels/Hidden", "nucleon/ok", "Trash"]))
    run_once(store, clf, j, cfg)
    assert [l for _, l in store.labels] == ["nucleon/ok"]


def test_r002_changed_uidvalidity_is_vanished_not_acted_on(tmp_path):
    store, j = FakeMailStore(), _journal(tmp_path)
    store.seed_inbox(_meta(), "body")
    refs = store.list_window("INBOX", 7, 10)
    store.set_uidvalidity("INBOX", 2)  # server renumbered the folder after listing
    clf = FakeClassifier(_cls(needs_reply=True, draft_body="Thanks", category="newsletter"))
    cfg = _cfg(auto_trash_enabled=True, auto_trash_categories=["newsletter"])
    report = run_once(store, clf, j, cfg)
    assert refs and report.vanished == 1 and report.errors == 0 and report.processed == 0
    assert store.appended_drafts == [] and store.labels == [] and store.moved_to_trash == []
    assert j._store.entries() == []  # stale ref is not recorded as processed


def test_r002_stale_ref_during_actions_stops_further_actions(tmp_path):
    """UIDVALIDITY flips between fetch and the label/trash step: no UID-based write."""
    store, j = FakeMailStore(), _journal(tmp_path)
    store.seed_inbox(_meta(), "body")
    real_fetch = store.fetch_body_text

    def flip(ref):
        text = real_fetch(ref)
        store.set_uidvalidity("INBOX", 9)
        return text

    store.fetch_body_text = flip  # type: ignore[method-assign]
    cfg = _cfg(auto_trash_enabled=True, auto_trash_categories=["newsletter"])
    report = run_once(store, FakeClassifier(_cls(category="newsletter")), j, cfg)
    assert report.vanished == 1 and store.labels == [] and store.moved_to_trash == []
