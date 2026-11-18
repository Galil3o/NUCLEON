"""R-013: an aborted append (no trailing newline) must never swallow the next entry."""
import json
from datetime import datetime, timezone

from nucleon.analysis import AnalysisLog, Stage2Record, Stage3Confirmation, Stage3Store
from nucleon.appendlog import append_line
from nucleon.journal import FileJournalStore
from nucleon.marker import FileMarkerStore, MarkerEntry
from nucleon.recipients import FileLearnedStore, LearnedEntry
from nucleon.models import JournalEntry



def make_entry(ref_key):
    return JournalEntry(ref_key=ref_key, account_hash="acct", mailbox="INBOX", uid=7,
                        message_id_hash="mid", raw_hash="raw", action="classified",
                        status="ok", ts=datetime(2026, 1, 1, tzinfo=timezone.utc))


TS = datetime(2026, 1, 1, tzinfo=timezone.utc)
TORN = '{"ref_key": "trunc'  # no newline: an aborted write


def _torn(path):
    with open(path, "a", encoding="utf-8") as f:
        f.write(TORN)


def test_append_line_terminates_unterminated_last_record(tmp_path):
    p = tmp_path / "x.jsonl"
    p.write_text('{"a": 1}\n' + TORN, encoding="utf-8")
    append_line(str(p), '{"b": 2}')
    lines = p.read_text().split("\n")
    assert lines == ['{"a": 1}', TORN, '{"b": 2}', ""]


def test_append_line_leaves_clean_file_untouched_and_creates_missing(tmp_path):
    p = tmp_path / "sub" / "x.jsonl"
    append_line(str(p), "one")
    append_line(str(p), "two")
    assert p.read_text() == "one\ntwo\n"


def test_append_line_on_empty_file(tmp_path):
    p = tmp_path / "x.jsonl"
    p.write_text("")
    append_line(str(p), "one")
    assert p.read_text() == "one\n"


def test_journal_keeps_entry_after_torn_record(tmp_path):
    path = tmp_path / "j.jsonl"
    store = FileJournalStore(str(path))
    store.append(make_entry("good"))
    _torn(path)
    store.append(make_entry("next"))
    assert [e.ref_key for e in store.entries()] == ["good", "next"]


def test_marker_keeps_entry_after_torn_record(tmp_path):
    path = tmp_path / "m.jsonl"
    s = FileMarkerStore(str(path))
    s.append(MarkerEntry(key="k1", status="ok", ts=TS))
    _torn(path)
    s.append(MarkerEntry(key="k2", status="ok", ts=TS))
    assert [e.key for e in s.entries()] == ["k1", "k2"]


def test_learned_store_keeps_entry_after_torn_record(tmp_path):
    path = tmp_path / "l.jsonl"
    s = FileLearnedStore(str(path))
    e1 = LearnedEntry(account_id="a", address="a@b.c", origin="o1", ts=TS)
    e2 = LearnedEntry(account_id="a", address="d@e.f", origin="o2", ts=TS)
    s.append(e1)
    _torn(path)
    s.append(e2)
    assert s.entries() == [e1, e2]


def test_analysis_log_keeps_record_after_torn_record(tmp_path):
    path = tmp_path / "a.jsonl"
    log = AnalysisLog(str(path))
    log.record_stage2(failed_stage="fetch", exc=OSError("x"), duration_ms=1,
                      ref_key="r", account_hash="h")
    _torn(path)
    log.record_stage2(failed_stage="classify", exc=OSError("x"), duration_ms=1,
                      ref_key="r", account_hash="h")
    good = []
    for line in path.read_text().splitlines():
        try:
            good.append(Stage2Record.model_validate_json(line))
        except ValueError:
            pass
    assert [r.failed_stage for r in good] == ["fetch", "classify"]


def test_stage3_store_keeps_record_after_torn_record(tmp_path):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    path = tmp_path / "s3.jsonl"
    s = Stage3Store(str(path), verbose_enabled=True, clock=lambda: now)
    c = Stage3Confirmation.explicit("a" * 16, ttl_seconds=60, now=now)
    s.write(c, {"k": "v1"})
    _torn(path)
    s.write(c, {"k": "v2"})
    parsed = []
    for line in path.read_text().splitlines():
        try:
            parsed.append(json.loads(line))
        except ValueError:
            pass
    assert [p["content"]["k"] for p in parsed] == ["v1", "v2"]
