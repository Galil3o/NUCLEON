import email

from nucleon.actions import execute
from nucleon.mailstore.fake import FakeMailStore
from nucleon.models import ApplyLabel, DeleteToTrash, MessageRef, StageDraft


def _ref() -> MessageRef:
    return MessageRef.from_source("a", "INBOX", 1, 7, "<m@x>", b"raw")


def _draft(**kw) -> StageDraft:
    base = dict(
        ref=_ref(),
        subject="Re: Hi",
        body="Thanks!",
        to="sender@x.com",
        in_reply_to="<m@x>",
        references=("<r0@x>", "<m@x>"),
        target="drafts",
        label="nucleon/proposed",
    )
    base.update(kw)
    return StageDraft(**base)


def test_apply_label_sets_label():
    s = FakeMailStore()
    execute(ApplyLabel(ref=_ref(), label="l1"), s)
    assert s.labels == [(_ref(), "l1")]


def test_stage_draft_appends_to_target_with_threading_headers():
    s = FakeMailStore()
    execute(_draft(), s)
    assert len(s.appended_drafts) == 1
    target, raw = s.appended_drafts[0]
    assert target == "drafts"
    msg = email.message_from_bytes(raw)
    assert msg["Subject"] == "Re: Hi"
    assert msg["To"] == "sender@x.com"
    assert msg["In-Reply-To"] == "<m@x>"
    assert msg["References"] == "<r0@x> <m@x>"
    assert msg.get_payload().strip() == "Thanks!"
    new_ref = MessageRef.from_source("fake", "drafts", 1, 1, None, raw)
    assert s.labels == [(new_ref, "nucleon/proposed")]


def test_stage_draft_uses_configured_target():
    s = FakeMailStore()
    execute(_draft(target="outbox"), s)
    assert s.appended_drafts[0][0] == "outbox"


def test_stage_draft_omits_optional_headers_when_absent():
    s = FakeMailStore()
    execute(_draft(to=None, in_reply_to=None, references=(), label=None), s)
    msg = email.message_from_bytes(s.appended_drafts[0][1])
    assert msg["To"] is None and msg["In-Reply-To"] is None and msg["References"] is None
    assert s.labels == []


def test_delete_to_trash_moves_to_trash():
    s = FakeMailStore()
    execute(DeleteToTrash(ref=_ref()), s)
    assert s.moved_to_trash == [_ref()]
