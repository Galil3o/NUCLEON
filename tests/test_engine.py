import hashlib

from nucleon.config import Config
from nucleon.engine import ReconcileReport, RunReport, diagnose_partial, reconcile, run_once
from nucleon.marker import FileMarkerStore, ProcessingMarker
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


def _marker(tmp_path) -> ProcessingMarker:
    """A fresh marker instance over the same file: also proves durability."""
    return ProcessingMarker(FileMarkerStore(str(tmp_path / "marker.jsonl")))


def test_run_once_labels_and_stages_one_draft(tmp_path):
    store, j = FakeMailStore(), _journal(tmp_path)
    store.seed_inbox(_meta(), "body")
    clf = FakeClassifier(_cls(needs_reply=True, draft_body="Thanks", labels=["nucleon/l1", "nucleon/l2"]))
    report = run_once(store, clf, j, _cfg(), _marker(tmp_path))
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
    report = run_once(store, FakeClassifier(_cls(category="newsletter")), j, cfg, _marker(tmp_path))
    assert store.moved_to_trash == [m.ref]
    assert report.trashed == 1
    assert [e.action for e in j._store.entries()] == ["trashed"]


def test_run_once_is_idempotent(tmp_path):
    store, j = FakeMailStore(), _journal(tmp_path)
    store.seed_inbox(_meta(), "body")
    clf = FakeClassifier(_cls(needs_reply=True, draft_body="Thanks"))
    run_once(store, clf, j, _cfg(), _marker(tmp_path))
    report = run_once(store, clf, j, _cfg(), _marker(tmp_path))
    assert report.processed == 0 and report.skipped == 1
    assert len(store.appended_drafts) == 1
    assert len(j._store.entries()) == 1


def test_run_once_classified_only(tmp_path):
    store, j = FakeMailStore(), _journal(tmp_path)
    store.seed_inbox(_meta(), "body")
    run_once(store, FakeClassifier(_cls()), j, _cfg(), _marker(tmp_path))
    assert [e.action for e in j._store.entries()] == ["classified"]


def test_null_journal_drafted_unsent_empty():
    assert NullJournal().drafted_unsent() == []


def _drafted_setup(tmp_path):
    store, j = FakeMailStore(), _journal(tmp_path)
    store.seed_inbox(_meta(), "body")
    run_once(store, FakeClassifier(_cls(needs_reply=True, draft_body="Hi")), j, _cfg(), _marker(tmp_path))
    assert len(j.drafted_unsent()) == 1
    return store, j


def test_reconcile_conversation_match_is_only_an_observed_reply(tmp_path):
    store, j = _drafted_setup(tmp_path)
    sent_ref = MessageRef.from_source("a", "Sent", 1, 9, "<out@x>", b"s")
    store.seed_sent(
        MessageMeta(ref=sent_ref, subject="Re: Hello", from_addr="d@e.f",
                    to_addrs=["a@b.c"], in_reply_to="<in@x>")
    )
    assert hashlib.sha256(b"<in@x>").hexdigest() == j.drafted_unsent()[0].message_id_hash
    # R-006: a reply that merely references the original is NOT proof of "sent".
    assert reconcile(store, j, _cfg()) == ReconcileReport(sent=0, observed=1)
    assert len(j.drafted_unsent()) == 1


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
    assert reconcile(store, j, _cfg()) == ReconcileReport(0, 0)
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
    report = run_once(store, clf, j, _cfg(), _marker(tmp_path))
    assert report.processed == 2 and report.errors == 1 and report.drafted == 2
    entries = j._store.entries()
    assert len(entries) == 3  # exactly one entry per ref
    bad = [e for e in entries if e.uid == 2]
    assert len(bad) == 1 and bad[0].action == "classified" and bad[0].status == "error"
    assert sorted(e.status for e in entries) == ["error", "ok", "ok"]


def test_errored_message_is_not_reprocessed(tmp_path):
    store, j, clf = _flaky_setup(tmp_path)
    run_once(store, clf, j, _cfg(), _marker(tmp_path))
    drafts = len(store.appended_drafts)
    second = run_once(store, clf, j, _cfg(), _marker(tmp_path))
    assert second.skipped == 3 and second.processed == 0 and second.errors == 0
    assert len(store.appended_drafts) == drafts
    assert len(j._store.entries()) == 3


def test_error_entry_has_fixed_status_and_no_exception_text(tmp_path):
    store, j, clf = _flaky_setup(tmp_path)
    run_once(store, clf, j, _cfg(), _marker(tmp_path))
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
    r1 = run_once(store, clf, j, _cfg(), _marker(tmp_path))
    r2 = run_once(store, clf, j, _cfg(), _marker(tmp_path))
    assert r1.errors == 1 and r2.skipped == 1 and calls["n"] == 1


def test_r001_engine_drops_labels_on_provider_system_folders(tmp_path):
    store, j = FakeMailStore(), _journal(tmp_path)
    store.system_folder_names = frozenset({"Labels/Hidden"})
    store.seed_inbox(_meta(), "body")
    cfg = _cfg()
    cfg = cfg.model_copy(update={"labels": cfg.labels.model_copy(update={"allowed": ["Labels/Hidden"]})})
    clf = FakeClassifier(_cls(labels=["Labels/Hidden", "nucleon/ok", "Trash"]))
    run_once(store, clf, j, cfg, _marker(tmp_path))
    assert [l for _, l in store.labels] == ["nucleon/ok"]


def test_r002_changed_uidvalidity_is_vanished_not_acted_on(tmp_path):
    store, j = FakeMailStore(), _journal(tmp_path)
    store.seed_inbox(_meta(), "body")
    refs = store.list_window("INBOX", 7, 10)
    store.set_uidvalidity("INBOX", 2)  # server renumbered the folder after listing
    clf = FakeClassifier(_cls(needs_reply=True, draft_body="Thanks", category="newsletter"))
    cfg = _cfg(auto_trash_enabled=True, auto_trash_categories=["newsletter"])
    report = run_once(store, clf, j, cfg, _marker(tmp_path))
    assert refs and report.vanished == 1 and report.errors == 0 and report.processed == 0
    assert store.appended_drafts == [] and store.labels == [] and store.moved_to_trash == []
    assert j._store.entries() == []  # stale ref is not recorded as processed


def test_r002_stale_ref_during_actions_stops_further_actions(tmp_path):
    """UIDVALIDITY flips between fetch and the label/trash step: no UID-based write."""
    store, j = FakeMailStore(), _journal(tmp_path)
    store.seed_inbox(_meta(), "body")
    real_fetch = store.fetch_body

    def flip(ref):
        got = real_fetch(ref)
        store.set_uidvalidity("INBOX", 9)
        return got

    store.fetch_body = flip  # type: ignore[method-assign]
    cfg = _cfg(auto_trash_enabled=True, auto_trash_categories=["newsletter"])
    report = run_once(store, FakeClassifier(_cls(category="newsletter")), j, cfg, _marker(tmp_path))
    assert report.vanished == 1 and store.labels == [] and store.moved_to_trash == []


def test_r003_default_config_nulljournal_second_run_creates_no_duplicates(tmp_path):
    """The R-003 problem: journal off (default) must still be idempotent."""
    store = FakeMailStore()
    store.seed_inbox(_meta(), "body")
    clf = FakeClassifier(_cls(needs_reply=True, draft_body="Thanks"))
    j = NullJournal()
    r1 = run_once(store, clf, j, _cfg(), _marker(tmp_path))
    r2 = run_once(store, clf, j, _cfg(), _marker(tmp_path))
    assert r1.drafted == 1 and r2.processed == 0 and r2.skipped == 1
    assert len(store.appended_drafts) == 1
    # one label for the message + one for the draft; none added by the 2nd run
    assert [l for _, l in store.labels] == ["nucleon/l1", "nucleon/proposed"]


def test_r003_marker_written_even_when_journal_enabled(tmp_path):
    store, j = FakeMailStore(), _journal(tmp_path)
    m = _meta()
    store.seed_inbox(m, "body")
    run_once(store, FakeClassifier(_cls()), j, _cfg(), _marker(tmp_path))
    assert len(j._store.entries()) == 1  # journal (audit) ...
    assert _marker(tmp_path).is_done(m.ref.processing_key())  # ... on top of the marker


def test_r003_run_without_marker_is_blocked(tmp_path):
    import pytest

    store = FakeMailStore()
    store.seed_inbox(_meta(), "body")
    clf = FakeClassifier(_cls(needs_reply=True, draft_body="Thanks"))
    with pytest.raises(ValueError):
        run_once(store, clf, NullJournal(), _cfg(), None)  # type: ignore[arg-type]
    assert store.appended_drafts == [] and store.labels == []


def test_r003_marker_set_before_writes_so_a_crash_cannot_duplicate(tmp_path):
    store = FakeMailStore()
    m = _meta()
    store.seed_inbox(m, "body")

    def crash(*a, **k):
        raise KeyboardInterrupt  # not an Exception: bypasses the error handler

    store.append_draft = crash  # type: ignore[method-assign]
    clf = FakeClassifier(_cls(needs_reply=True, draft_body="Thanks"))
    import pytest

    with pytest.raises(KeyboardInterrupt):
        run_once(store, clf, NullJournal(), _cfg(), _marker(tmp_path))
    r = run_once(store, clf, NullJournal(), _cfg(), _marker(tmp_path))
    assert r.skipped == 1 and r.processed == 0 and store.appended_drafts == []


def test_r003_new_uidvalidity_is_a_new_identity(tmp_path):
    store = FakeMailStore()
    store.seed_inbox(_meta(), "body")
    clf = FakeClassifier(_cls())
    run_once(store, clf, NullJournal(), _cfg(), _marker(tmp_path))
    store2 = FakeMailStore()
    ref = MessageRef.from_source("a", "INBOX", 2, 1, "<in@x>", b"raw1")
    store2.seed_inbox(MessageMeta(ref=ref, subject="s", from_addr="a@b.c", to_addrs=[],
                                  message_id="<in@x>"), "body")
    store2.set_uidvalidity("INBOX", 2)
    r = run_once(store2, clf, NullJournal(), _cfg(), _marker(tmp_path))
    assert r.processed == 1


def test_r003_errored_message_is_marked_and_not_retried(tmp_path):
    store, j, clf = _flaky_setup(tmp_path)
    run_once(store, clf, NullJournal(), _cfg(), _marker(tmp_path))
    n = len(store.appended_drafts)
    r = run_once(store, clf, NullJournal(), _cfg(), _marker(tmp_path))
    assert r.skipped == 3 and len(store.appended_drafts) == n


def _limited_cfg(limit):
    cfg = _cfg()
    return cfg.model_copy(update={"mail": cfg.mail.model_copy(update={"limit": limit})})


def test_r005_limit_applies_after_marker_filter_oldest_not_starved(tmp_path):
    """3 messages, limit 2: across runs the oldest is reached; none is skipped forever."""
    store = FakeMailStore()
    for uid in (1, 2, 3):  # uid 1 is the oldest
        store.seed_inbox(_meta(uid=uid, mid="<m%d@x>" % uid), "body")
    clf = FakeClassifier(_cls(needs_reply=True, draft_body="Thanks"))
    cfg = _limited_cfg(2)
    reports = []
    for _ in range(3):
        reports.append(run_once(store, clf, NullJournal(), cfg, _marker(tmp_path)))
    assert [r.processed for r in reports] == [2, 1, 0]
    assert len(store.appended_drafts) == 3  # every message handled exactly once
    assert {r.uid for r, _ in store.labels if r.uid in (1, 2, 3)} == {1, 2, 3}


def test_r005_already_processed_do_not_consume_the_limit(tmp_path):
    store = FakeMailStore()
    for uid in (1, 2, 3, 4):
        store.seed_inbox(_meta(uid=uid, mid="<m%d@x>" % uid), "body")
    clf = FakeClassifier(_cls())
    r1 = run_once(store, clf, NullJournal(), _limited_cfg(2), _marker(tmp_path))
    r2 = run_once(store, clf, NullJournal(), _limited_cfg(2), _marker(tmp_path))
    assert r1.processed == 2 and r2.processed == 2 and r2.skipped == 2


def test_r005_limit_zero_processes_nothing(tmp_path):
    store = FakeMailStore()
    store.seed_inbox(_meta(), "body")
    r = run_once(store, FakeClassifier(_cls()), NullJournal(), _limited_cfg(0), _marker(tmp_path))
    assert r.processed == 0 and store.labels == []


# -- R-004: partial actions, stable draft reference, read-only diagnosis -------
class _WriteGuard:
    """Wraps a store; every mailbox WRITE raises, reads pass through (and are logged)."""

    WRITES = {"append_draft", "set_label", "move_to_trash"}

    def __init__(self, inner):
        self._inner, self.calls = inner, []

    def __getattr__(self, name):
        self.calls.append(name)
        if name in self.WRITES:
            raise AssertionError("diagnosis must not write: " + name)
        return getattr(self._inner, name)


def _partial_store(tmp_path, fail_label_with):
    store = FakeMailStore()
    store.seed_inbox(_meta(), "body")
    real = store.set_label

    def flaky(ref, label):
        if label == "nucleon/proposed":
            raise fail_label_with
        real(ref, label)

    store.set_label = flaky  # type: ignore[method-assign]
    return store


def test_r004_draft_ref_and_step_are_durable_before_the_next_step(tmp_path):
    """Crash right after APPEND (during the draft label): marker already holds the ref."""
    store = _partial_store(tmp_path, KeyboardInterrupt())
    m = _meta()
    clf = FakeClassifier(_cls(needs_reply=True, draft_body="Thanks"))
    import pytest

    with pytest.raises(KeyboardInterrupt):
        run_once(store, clf, NullJournal(), _cfg(), _marker(tmp_path))
    e = _marker(tmp_path).entry(m.ref.processing_key())  # fresh instance = from disk
    assert e.status == "started"
    assert "StageDraft:appended" in e.steps and "ApplyLabel" in e.steps
    assert e.draft is not None and e.draft.confirmed and e.draft.uid == 1
    assert e.draft.message_id_hash


def test_r004_error_after_append_keeps_steps_and_draft_and_does_not_retry(tmp_path):
    store = _partial_store(tmp_path, RuntimeError("boom"))
    m = _meta()
    clf = FakeClassifier(_cls(needs_reply=True, draft_body="Thanks"))
    r1 = run_once(store, clf, NullJournal(), _cfg(), _marker(tmp_path))
    e = _marker(tmp_path).entry(m.ref.processing_key())
    assert r1.errors == 1 and e.status == "error"
    assert "StageDraft:appended" in e.steps and e.draft is not None and e.draft.confirmed
    r2 = run_once(store, clf, NullJournal(), _cfg(), _marker(tmp_path))
    assert r2.skipped == 1 and r2.processed == 0 and r2.errors == 0
    assert len(store.appended_drafts) == 1  # conservative default: no auto-retry


def test_r004_intent_recorded_before_append_for_unclear_outcome(tmp_path):
    """APPEND succeeds server-side but the client sees an error: intent is on disk."""
    store = FakeMailStore()
    m = _meta()
    store.seed_inbox(m, "body")
    real = store.append_draft

    def lost_reply(target, mime):
        real(target, mime)
        raise TimeoutError("connection dropped after APPEND")

    store.append_draft = lost_reply  # type: ignore[method-assign]
    clf = FakeClassifier(_cls(needs_reply=True, draft_body="Thanks"))
    run_once(store, clf, NullJournal(), _cfg(), _marker(tmp_path))
    e = _marker(tmp_path).entry(m.ref.processing_key())
    assert e.status == "error" and e.draft is not None and not e.draft.confirmed
    assert "StageDraft:appended" not in e.steps
    # read-only diagnosis can tell that the draft is in fact there
    guard = _WriteGuard(store)
    findings = diagnose_partial(guard, _marker(tmp_path))
    assert [(f.key, f.draft_state) for f in findings] == [(m.ref.processing_key(), "present")]


def test_r004_diagnosis_is_read_only_and_reports_absent_and_unknown(tmp_path):
    store = FakeMailStore()
    m1, m2 = _meta(uid=1, mid="<a@x>"), _meta(uid=2, mid="<b@x>")
    store.seed_inbox(m1, "b"), store.seed_inbox(m2, "b")
    clf = FakeClassifier(_cls(needs_reply=True, draft_body="Thanks"))
    real = store.append_draft

    def fail_before_append(target, mime):
        raise ConnectionError("down")

    store.append_draft = fail_before_append  # type: ignore[method-assign]
    run_once(store, clf, NullJournal(), _cfg(), _marker(tmp_path))
    store.append_draft = real  # type: ignore[method-assign]
    guard = _WriteGuard(store)
    f = {x.key: x.draft_state for x in diagnose_partial(guard, _marker(tmp_path))}
    assert set(f.values()) == {"absent"}  # intent recorded, nothing on the server
    assert not (set(guard.calls) & _WriteGuard.WRITES)

    def broken_find(target, message_id):
        raise ConnectionError("down")

    store.find_draft = broken_find  # type: ignore[method-assign]
    f = {x.key: x.draft_state for x in diagnose_partial(_WriteGuard(store), _marker(tmp_path))}
    assert set(f.values()) == {"unknown"}


def test_r004_diagnosis_does_not_touch_completed_messages_or_write_marker(tmp_path):
    store = FakeMailStore()
    store.seed_inbox(_meta(), "body")
    run_once(store, FakeClassifier(_cls(needs_reply=True, draft_body="T")), NullJournal(),
             _cfg(), _marker(tmp_path))
    path = tmp_path / "marker.jsonl"
    before = path.read_text()
    assert diagnose_partial(_WriteGuard(store), _marker(tmp_path)) == []
    assert path.read_text() == before


def test_r004_journal_drafted_entry_carries_draft_reference(tmp_path):
    store, j = FakeMailStore(), _journal(tmp_path)
    store.seed_inbox(_meta(), "body")
    run_once(store, FakeClassifier(_cls(needs_reply=True, draft_body="Hi")), j, _cfg(), _marker(tmp_path))
    e = j._store.entries()[0]
    assert e.draft_message_id_hash and e.draft_confirmed is True


# -- R-006: exact draft identity -> "sent"; conversation only -> "observed reply"
def _sent_meta(mid, irt=None, uid=9):
    ref = MessageRef.from_source("a", "Sent", 1, uid, mid, b"s%d" % uid)
    return MessageMeta(ref=ref, subject="Re: Hello", from_addr="d@e.f", to_addrs=["a@b.c"],
                       in_reply_to=irt, message_id=mid)


def _own_draft_mid():
    from nucleon.actions import draft_message_id

    return draft_message_id(_meta().ref.processing_key())


def test_r006_separately_composed_reply_is_observed_not_sent(tmp_path):
    store, j = _drafted_setup(tmp_path)
    store.seed_sent(_sent_meta("<handwritten@x>", irt="<in@x>"))
    assert reconcile(store, j, _cfg()) == ReconcileReport(sent=0, observed=1)
    assert len(j.drafted_unsent()) == 1  # the proposal is still open
    e = j._store.entries()[-1]
    assert e.action == "observed_reply" and e.status == "observed_reply"
    # idempotent: no second observation record
    n = len(j._store.entries())
    assert reconcile(store, j, _cfg()) == ReconcileReport(0, 0)
    assert len(j._store.entries()) == n


def test_r006_draft_message_id_in_sent_means_sent(tmp_path):
    store, j = _drafted_setup(tmp_path)
    store.seed_sent(_sent_meta(_own_draft_mid(), irt="<in@x>"))
    assert reconcile(store, j, _cfg()) == ReconcileReport(sent=1, observed=0)
    assert j.drafted_unsent() == []
    assert j._store.entries()[-1].status == "sent"


def test_r006_observed_then_real_send_upgrades_to_sent(tmp_path):
    store, j = _drafted_setup(tmp_path)
    store.seed_sent(_sent_meta("<handwritten@x>", irt="<in@x>", uid=8))
    reconcile(store, j, _cfg())
    store.seed_sent(_sent_meta(_own_draft_mid(), irt="<in@x>", uid=9))
    assert reconcile(store, j, _cfg()) == ReconcileReport(sent=1, observed=0)
    assert j.drafted_unsent() == []


def test_r006_unconfirmed_draft_is_never_reported_as_sent(tmp_path):
    from nucleon.models import JournalEntry
    from datetime import datetime, timezone

    store, j = FakeMailStore(), _journal(tmp_path)
    m = _meta()
    j.record(JournalEntry(
        ref_key=m.ref.key(), account_hash="a", mailbox="INBOX", uid=1,
        message_id_hash=m.ref.message_id_hash, raw_hash="r", action="drafted", status="ok",
        ts=datetime(2020, 1, 1, tzinfo=timezone.utc),
        draft_message_id_hash=hashlib.sha256(_own_draft_mid().encode()).hexdigest(),
        draft_confirmed=False,
    ))
    store.seed_sent(_sent_meta(_own_draft_mid(), irt="<in@x>"))
    assert reconcile(store, j, _cfg()) == ReconcileReport(sent=0, observed=1)


def test_r006_legacy_entry_without_draft_id_falls_back_to_observed(tmp_path):
    from nucleon.models import JournalEntry
    from datetime import datetime, timezone

    store, j = FakeMailStore(), _journal(tmp_path)
    m = _meta()
    j.record(JournalEntry(
        ref_key=m.ref.key(), account_hash="a", mailbox="INBOX", uid=1,
        message_id_hash=m.ref.message_id_hash, raw_hash="r", action="drafted", status="ok",
        ts=datetime(2020, 1, 1, tzinfo=timezone.utc),
    ))
    store.seed_sent(_sent_meta("<x@x>", irt="<in@x>"))
    assert reconcile(store, j, _cfg()) == ReconcileReport(sent=0, observed=1)


def test_r006_reconcile_has_its_own_timestamp_not_the_drafts(tmp_path):
    from datetime import datetime, timezone

    store, j = _drafted_setup(tmp_path)
    drafted = j._store.entries()[0]
    store.seed_sent(_sent_meta(_own_draft_mid()))
    t0 = datetime.now(timezone.utc)
    reconcile(store, j, _cfg())
    sent = j._store.entries()[-1]
    assert sent.action == "marked_sent" and sent.ts != drafted.ts and sent.ts >= t0


def test_r006_reconcile_is_read_only_on_the_mailbox(tmp_path):
    store, j = _drafted_setup(tmp_path)
    store.seed_sent(_sent_meta(_own_draft_mid()))
    guard = _WriteGuard(store)
    reconcile(guard, j, _cfg())
    assert not (set(guard.calls) & _WriteGuard.WRITES)
