from datetime import datetime, timezone

import json

import pytest

from nucleon.config import JournalConfig
from nucleon.journal import (
    FileJournalStore,
    NullJournal,
    StoredJournal,
    build_journal,
)
from nucleon.models import JournalEntry


def make_entry(ref_key="r1", action="classified", status="ok"):
    return JournalEntry(
        ref_key=ref_key,
        account_hash="acct",
        mailbox="INBOX",
        uid=7,
        message_id_hash="mid",
        raw_hash="raw",
        action=action,
        status=status,
        ts=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def test_null_journal_reports_unprocessed():
    j = NullJournal()
    j.record(make_entry())
    j.mark_sent("r1")
    assert j.has_processed("r1") is False


def test_file_store_append_and_entries(tmp_path):
    store = FileJournalStore(str(tmp_path / "sub" / "j.jsonl"))
    assert store.entries() == []
    e = make_entry()
    store.append(e)
    store.append(make_entry("r2"))
    assert store.entries()[0] == e
    assert [x.ref_key for x in store.entries()] == ["r1", "r2"]


def test_stored_journal_detects_processed(tmp_path):
    j = StoredJournal(FileJournalStore(str(tmp_path / "j.jsonl")))
    assert j.has_processed("r1") is False
    j.record(make_entry())
    assert j.has_processed("r1") is True
    assert j.has_processed("other") is False
    j.mark_sent("r1")
    last = j._store.entries()[-1]
    assert last.action == "marked_sent" and last.status == "sent"
    assert last.uid == 7 and last.raw_hash == "raw"


def test_idempotent_across_reload(tmp_path):
    p = str(tmp_path / "j.jsonl")
    StoredJournal(FileJournalStore(p)).record(make_entry())
    assert StoredJournal(FileJournalStore(p)).has_processed("r1") is True


def test_file_contains_no_content(tmp_path):
    p = tmp_path / "j.jsonl"
    j = StoredJournal(FileJournalStore(str(p)))
    # Seeded content strings are never part of an entry; guard the raw file.
    j.record(make_entry())
    j.mark_sent("r1")
    text = p.read_text()
    for secret in ("Secret subject", "hello body", "alice@example.com"):
        assert secret not in text
    for line in text.splitlines():
        if line.strip():
            assert set(json.loads(line).keys()) == set(JournalEntry.model_fields)


def test_build_journal_defaults_to_null():
    assert isinstance(build_journal(JournalConfig()), NullJournal)


def test_build_journal_file_backend(tmp_path):
    j = build_journal(JournalConfig(enabled=True, path=str(tmp_path / "j.jsonl")))
    assert isinstance(j, StoredJournal)


def test_build_journal_file_requires_path():
    with pytest.raises(ValueError, match="path"):
        build_journal(JournalConfig(enabled=True))


def test_mark_sent_unknown_ref_is_noop(tmp_path):
    store = FileJournalStore(str(tmp_path / "j.jsonl"))
    j = StoredJournal(store)
    j.record(make_entry())
    before = store.entries()
    j.mark_sent("unknown")
    assert store.entries() == before


def test_file_store_skips_corrupt_line(tmp_path):
    path = tmp_path / "j.jsonl"
    store = FileJournalStore(str(path))
    store.append(make_entry("good"))
    with open(path, "a", encoding="utf-8") as f:
        f.write('{"ref_key": "trunc\n')
    got = store.entries()
    assert [e.ref_key for e in got] == ["good"]


def test_mark_sent_uses_its_own_timestamp(tmp_path):
    j = StoredJournal(FileJournalStore(str(tmp_path / "j.jsonl")))
    j.record(make_entry(action="drafted"))
    j.mark_sent("r1")
    last = j._store.entries()[-1]
    assert last.action == "marked_sent" and last.ts > datetime(2026, 1, 2, tzinfo=timezone.utc)


def test_mark_observed_keeps_draft_open_and_is_idempotent(tmp_path):
    j = StoredJournal(FileJournalStore(str(tmp_path / "j.jsonl")))
    j.record(make_entry(action="drafted"))
    assert j.mark_observed("r1") is True
    assert j.mark_observed("r1") is False
    assert [e.ref_key for e in j.drafted_unsent()] == ["r1"]
    assert j._store.entries()[-1].status == "observed_reply"
    assert j.mark_observed("unknown") is False
    assert NullJournal().mark_observed("r1") is False
