import hashlib
import typing

from nucleon.models import Action, JournalEntry, MessageRef


def test_message_ref_hashing_is_deterministic():
    raw = b"raw bytes"
    a = MessageRef.from_source("acct", "INBOX", 7, 42, "<id@x>", raw)
    b = MessageRef.from_source("acct", "INBOX", 7, 42, "<id@x>", raw)
    assert a == b
    assert a.message_id_hash == hashlib.sha256(b"<id@x>").hexdigest()
    assert a.raw_hash == hashlib.sha256(raw).hexdigest()
    assert a.key() == "acct:INBOX:7:42"
    assert MessageRef.from_source("acct", "INBOX", 7, 42, None, raw).message_id_hash is None


def test_journal_entry_has_no_content_fields():
    assert set(JournalEntry.model_fields) & {"subject", "body", "from_addr", "to_addrs"} == set()


def test_action_union_has_no_send_or_permanent_delete():
    names = {t.__name__ for t in typing.get_args(Action)}
    assert names == {"ApplyLabel", "StageDraft", "DeleteToTrash"}
