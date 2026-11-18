from nucleon.mailstore.fake import FakeMailStore
from nucleon.models import MessageMeta, MessageRef


def _meta(uid: int, mailbox: str = "INBOX") -> MessageMeta:
    ref = MessageRef.from_source("acct", mailbox, 1, uid, f"<{uid}@x>", b"raw%d" % uid)
    return MessageMeta(ref=ref, subject=f"s{uid}", from_addr="a@x.com", to_addrs=["b@x.com"])


def test_list_window_returns_seeded_refs():
    s = FakeMailStore()
    metas = [_meta(i) for i in range(1, 4)]
    for m in metas:
        s.seed_inbox(m, "body")
    assert s.list_window("INBOX", 7, 10) == [m.ref for m in metas]
    assert s.list_window("INBOX", 7, 2) == [m.ref for m in metas[:2]]
    assert s.fetch_meta(metas[0].ref) == metas[0]


def test_fetch_body_text_does_not_change_flags():
    s = FakeMailStore()
    m = _meta(1)
    s.seed_inbox(m, "hello")
    before = set(s.seen)
    assert s.fetch_body_text(m.ref) == "hello"
    assert s.seen == before == set()


def test_append_draft_records_target_and_bytes():
    s = FakeMailStore()
    ref = s.append_draft("Drafts", b"MIME")
    assert s.appended_drafts == [("Drafts", b"MIME")]
    assert isinstance(ref, MessageRef) and ref.mailbox == "Drafts"


def test_set_label_records_label():
    s = FakeMailStore()
    m = _meta(1)
    s.set_label(m.ref, "Nucleon/Reply")
    assert s.labels == [(m.ref, "Nucleon/Reply")]


def test_move_to_trash_records_ref():
    s = FakeMailStore()
    m = _meta(1)
    s.move_to_trash(m.ref)
    assert s.moved_to_trash == [m.ref]


def test_list_sent_returns_seeded():
    s = FakeMailStore()
    m = _meta(1, "Sent")
    s.seed_sent(m)
    assert s.list_sent(30, 10) == [m]


def test_uidvalidity_change_makes_every_ref_access_stale():
    import pytest

    from nucleon.mailstore import StaleReferenceError

    s = FakeMailStore()
    m = _meta(1)
    s.seed_inbox(m, "body")
    s.set_uidvalidity("INBOX", 5)
    for call in (
        lambda: s.fetch_meta(m.ref),
        lambda: s.fetch_body_text(m.ref),
        lambda: s.set_label(m.ref, "nucleon/x"),
        lambda: s.move_to_trash(m.ref),
    ):
        with pytest.raises(StaleReferenceError):
            call()
    assert s.labels == [] and s.moved_to_trash == []
