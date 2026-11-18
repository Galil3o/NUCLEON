import json

import pytest

from nucleon.config import Config
from nucleon.marker import FileMarkerStore, MarkerEntry, ProcessingMarker, build_marker
from nucleon.models import MessageRef


def _ref(uid=1, validity=1, mid="<m@x>", account="a", mailbox="INBOX", raw=b"raw") -> MessageRef:
    return MessageRef.from_source(account, mailbox, validity, uid, mid, raw)


def test_processing_key_is_stable_and_identity_bound():
    base = _ref()
    assert base.processing_key() == _ref().processing_key()
    # raw bytes (e.g. flag/header-fetch noise) do not change the identity
    assert base.processing_key() == _ref(raw=b"other").processing_key()
    for other in (_ref(uid=2), _ref(validity=2), _ref(mid="<z@x>"),
                  _ref(account="b"), _ref(mailbox="Other"), _ref(mid=None)):
        assert other.processing_key() != base.processing_key()


def test_processing_key_has_no_plaintext_message_id():
    assert "m@x" not in _ref().processing_key()


def test_marker_persists_across_instances(tmp_path):
    path = str(tmp_path / "state" / "m.jsonl")
    m1 = ProcessingMarker(FileMarkerStore(path))
    k = _ref().processing_key()
    assert not m1.is_done(k)
    m1.mark(k, "ok", ("label", "draft"))
    assert m1.is_done(k)
    m2 = ProcessingMarker(FileMarkerStore(path))  # new process, same file
    assert m2.is_done(k) and not m2.is_done(_ref(uid=2).processing_key())
    assert m2.entry(k).steps == ("label", "draft")


def test_marker_file_holds_only_metadata(tmp_path):
    path = tmp_path / "m.jsonl"
    m = ProcessingMarker(FileMarkerStore(str(path)))
    m.mark(_ref(mid="<secret-id@x>").processing_key(), "ok", ())
    text = path.read_text()
    assert "secret-id" not in text
    assert set(json.loads(text)) == {"key", "status", "steps", "ts"}


def test_corrupt_line_is_tolerated(tmp_path):
    path = tmp_path / "m.jsonl"
    m = ProcessingMarker(FileMarkerStore(str(path)))
    k = _ref().processing_key()
    m.mark(k, "ok", ())
    with open(path, "a") as f:
        f.write('{"key": "half\n')
    assert ProcessingMarker(FileMarkerStore(str(path))).is_done(k)


def test_build_marker_is_always_on_with_default_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = Config.model_validate({
        "mail": {"mode": "imap", "host": "h", "port": 993, "username": "u"},
        "classifier": {"endpoint": "http://127.0.0.1:1", "model": "m"},
    })
    assert cfg.journal.enabled is False  # journal off ...
    m = build_marker(cfg.marker)         # ... marker still real
    k = _ref().processing_key()
    m.mark(k, "ok", ())
    assert build_marker(cfg.marker).is_done(k)


def test_marker_config_has_no_off_switch():
    with pytest.raises(ValueError):
        Config.model_validate({
            "mail": {"mode": "imap", "host": "h", "port": 993, "username": "u"},
            "classifier": {"endpoint": "e", "model": "m"},
            "marker": {"enabled": False},
        })
    assert MarkerEntry  # exported


def test_marker_keeps_draft_ref_and_carries_it_over(tmp_path):
    from nucleon.models import DraftRef

    path = str(tmp_path / "m.jsonl")
    m = ProcessingMarker(FileMarkerStore(path))
    k = _ref().processing_key()
    d = DraftRef(target="drafts", message_id_hash="ab" * 32, confirmed=True,
                 mailbox="Drafts", uidvalidity=7, uid=3)
    m.mark(k, "started", ("StageDraft:appended",), d)
    m.mark(k, "error", ("StageDraft:appended",))  # no draft given: carried over
    assert ProcessingMarker(FileMarkerStore(path)).entry(k).draft == d
    assert [e.key for e in m.unfinished()] == [k]
    m.mark(k, "ok", ("StageDraft",))
    assert m.unfinished() == []
