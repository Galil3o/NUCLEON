"""R-008: analysis Stage 2 is a fixed metadata schema; content only via Stage 3 confirmation."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from nucleon.analysis import (
    AnalysisLog,
    Stage2Record,
    Stage3Confirmation,
    Stage3NotConfirmed,
    Stage3Store,
    error_code_for,
)
from nucleon.config import Config
from nucleon.engine import run_once
from nucleon.journal import FileJournalStore, StoredJournal
from nucleon.mailstore.fake import FakeMailStore
from nucleon.marker import FileMarkerStore, ProcessingMarker
from nucleon.models import Classification, MessageMeta, MessageRef
from nucleon.quarantine import ClassifierOutputError
from nucleon.quarantine.fake import FakeClassifier

SECRET = "Subject: payroll-secret alice@example.com IMAP BAD [AUTHENTICATIONFAILED] hunter2"


def _cfg(**extra) -> Config:
    return Config.model_validate({
        "mail": {"mode": "imap", "host": "h", "port": 993, "username": "u"},
        "classifier": {"endpoint": "http://127.0.0.1:1", "model": "m"},
        **extra,
    })


def _meta(uid=1) -> MessageMeta:
    ref = MessageRef.from_source("a", "INBOX", 1, uid, "<in@x>", b"raw%d" % uid)
    return MessageMeta(ref=ref, subject="Hello", from_addr="a@b.c", to_addrs=["d@e.f"],
                       message_id="<in@x>")


class Boom:
    def __init__(self, exc):
        self._exc = exc

    def classify(self, meta, body):
        raise self._exc


def _run(tmp_path, clf, cfg=None, analysis="default"):
    store = FakeMailStore()
    store.seed_inbox(_meta(), "BODY-SECRET-TEXT")
    log = AnalysisLog(str(tmp_path / "analysis.jsonl")) if analysis == "default" else analysis
    j = StoredJournal(FileJournalStore(str(tmp_path / "j.jsonl")))
    m = ProcessingMarker(FileMarkerStore(str(tmp_path / "m.jsonl")))
    rep = run_once(store, clf, j, cfg or _cfg(), m, analysis=log)
    return rep, log


def test_stage2_schema_is_exactly_the_metadata_fields():
    assert set(Stage2Record.model_fields) == {
        "schema_version", "analysis_stage", "failed_stage", "error_code",
        "duration_ms", "ref_pseudonym", "account_pseudonym", "steps_completed", "ts",
    }


def test_stage2_record_rejects_content_fields_and_free_text():
    ok = dict(failed_stage="classify", error_code="value_error", duration_ms=3,
              ref_pseudonym="0" * 16, account_pseudonym="1" * 16,
              steps_completed=(), ts=datetime.now(timezone.utc))
    Stage2Record(**ok)
    for bad in ({"subject": "x"}, {"body": "x"}, {"exception_text": "x"}, {"traceback": "x"}):
        with pytest.raises(ValidationError):
            Stage2Record(**ok, **bad)
    with pytest.raises(ValidationError):
        Stage2Record(**{**ok, "error_code": SECRET})  # free text is not an error code
    with pytest.raises(ValidationError):
        Stage2Record(**{**ok, "ref_pseudonym": "alice@example.com"})
    with pytest.raises(ValidationError):
        Stage2Record(**{**ok, "failed_stage": "Subject: x"})


@pytest.mark.parametrize("exc,code", [
    (ClassifierOutputError(SECRET), "classifier_output_invalid"),
    (TimeoutError(SECRET), "io_error"),
    (ValueError(SECRET), "value_error"),
    (KeyError(SECRET), "lookup_error"),
    (RuntimeError(SECRET), "runtime_error"),
    (Exception(SECRET), "unexpected_error"),
])
def test_error_code_is_derived_from_type_only(exc, code):
    assert error_code_for(exc) == code


@pytest.mark.parametrize("exc", [
    ClassifierOutputError(SECRET), RuntimeError(SECRET), ValueError(SECRET), Exception(SECRET),
])
def test_content_bearing_error_leaks_nothing_anywhere(tmp_path, exc):
    rep, log = _run(tmp_path, Boom(exc), _cfg(analysis={"enabled": True}))
    assert rep.errors == 1
    blob = "".join(p.read_text() for p in tmp_path.iterdir())
    for leak in ("payroll-secret", "alice", "hunter2", "AUTHENTICATIONFAILED",
                 "BODY-SECRET-TEXT", "Hello", "a@b.c", "d@e.f", "Traceback"):
        assert leak not in blob
    lines = (tmp_path / "analysis.jsonl").read_text().splitlines()
    assert len(lines) == 1
    rec = Stage2Record.model_validate_json(lines[0])
    assert rec.failed_stage == "classify" and rec.analysis_stage == 2
    assert set(json.loads(lines[0])) <= set(Stage2Record.model_fields)


def test_failed_stage_reflects_where_it_failed(tmp_path):
    store = FakeMailStore()
    store.seed_inbox(_meta(), "b")

    def bad_fetch(ref):
        raise OSError(SECRET)

    store.fetch_meta = bad_fetch  # type: ignore[method-assign]
    log = AnalysisLog(str(tmp_path / "a.jsonl"))
    j = StoredJournal(FileJournalStore(str(tmp_path / "j.jsonl")))
    m = ProcessingMarker(FileMarkerStore(str(tmp_path / "m.jsonl")))
    run_once(store, FakeClassifier(Classification(
        category="c", labels=[], urgency="low", needs_reply=False)), j,
        _cfg(analysis={"enabled": True}), m, analysis=log)
    rec = Stage2Record.model_validate_json((tmp_path / "a.jsonl").read_text())
    assert rec.failed_stage == "fetch" and rec.error_code == "io_error"


def test_analysis_off_writes_nothing(tmp_path):
    rep, _ = _run(tmp_path, Boom(ValueError(SECRET)), _cfg(), analysis=None)
    assert rep.errors == 1 and not (tmp_path / "analysis.jsonl").exists()


def test_analysis_log_failure_never_breaks_the_run(tmp_path):
    class Broken:
        def record_stage2(self, *a, **k):
            raise OSError("disk full")

    rep, _ = _run(tmp_path, Boom(ValueError("x")), _cfg(analysis={"enabled": True}),
                  analysis=Broken())
    assert rep.errors == 1


# --- Stage 3: explicit confirmation, deleted promptly -----------------------
NOW = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)


def _store(tmp_path, verbose=True):
    return Stage3Store(str(tmp_path / "s3.jsonl"), verbose_enabled=verbose, clock=lambda: NOW)


def test_stage3_refuses_without_confirmation(tmp_path):
    s = _store(tmp_path)
    with pytest.raises(Stage3NotConfirmed):
        s.write(None, {"body": "secret"})
    assert not (tmp_path / "s3.jsonl").exists()


def test_stage3_refuses_when_verbose_toggle_is_off_even_if_confirmed(tmp_path):
    s = _store(tmp_path, verbose=False)
    c = Stage3Confirmation.explicit("0" * 16, ttl_seconds=60, now=NOW)
    with pytest.raises(Stage3NotConfirmed):
        s.write(c, {"body": "secret"})


def test_stage3_confirmation_is_bound_to_ref_and_expires(tmp_path):
    s = _store(tmp_path)
    c = Stage3Confirmation.explicit("a" * 16, ttl_seconds=60, now=NOW - timedelta(seconds=120))
    with pytest.raises(Stage3NotConfirmed):  # expired
        s.write(c, {"body": "x"}, ref_pseudonym="a" * 16)
    c = Stage3Confirmation.explicit("a" * 16, ttl_seconds=60, now=NOW)
    with pytest.raises(Stage3NotConfirmed):  # different message
        s.write(c, {"body": "x"}, ref_pseudonym="b" * 16)


def test_stage3_confirmed_write_then_purged_when_expired(tmp_path):
    clock = {"now": NOW}
    s = Stage3Store(str(tmp_path / "s3.jsonl"), verbose_enabled=True, clock=lambda: clock["now"])
    c = Stage3Confirmation.explicit("a" * 16, ttl_seconds=60, now=NOW)
    s.write(c, {"body": "secret"}, ref_pseudonym="a" * 16)
    assert "secret" in (tmp_path / "s3.jsonl").read_text()
    assert s.purge_expired() == 0
    clock["now"] = NOW + timedelta(seconds=61)
    assert s.purge_expired() == 1
    assert "secret" not in (tmp_path / "s3.jsonl").read_text()


def test_stage3_ttl_is_capped():
    with pytest.raises(ValueError):
        Stage3Confirmation.explicit("a" * 16, ttl_seconds=10**6, now=NOW)


def test_engine_never_writes_stage3_content(tmp_path):
    _run(tmp_path, Boom(ValueError(SECRET)), _cfg(analysis={"enabled": True, "verbose": True}))
    assert not (tmp_path / "s3.jsonl").exists()
