import email

from nucleon.actions import draft_message_id, execute
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
    new_ref = MessageRef.from_source("fake", "drafts", 1, 1, msg["Message-ID"], raw)
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


def test_stage_draft_has_deterministic_message_id_derived_from_source():
    s1, s2 = FakeMailStore(), FakeMailStore()
    execute(_draft(), s1)
    execute(_draft(), s2)
    m1 = email.message_from_bytes(s1.appended_drafts[0][1])["Message-ID"]
    m2 = email.message_from_bytes(s2.appended_drafts[0][1])["Message-ID"]
    assert m1 == m2 and m1.startswith("<nucleon-") and "m@x" not in m1
    assert draft_message_id(_ref().processing_key()) == m1


def test_stage_draft_reports_intent_before_and_ref_after_append_before_label():
    s = FakeMailStore()
    order = []
    real_label = s.set_label
    s.set_label = lambda r, l: (order.append("label"), real_label(r, l))  # type: ignore[method-assign]
    ref = execute(
        _draft(), s,
        on_draft_intent=lambda d: order.append(("intent", d.confirmed, len(s.appended_drafts))),
        on_draft_appended=lambda d: order.append(("appended", d.confirmed, d.uid)),
    )
    assert order == [("intent", False, 0), ("appended", True, 1), "label"]
    assert ref is not None and ref.uid == 1


def test_non_draft_actions_return_none_and_fire_no_draft_callbacks():
    s = FakeMailStore()
    assert execute(ApplyLabel(ref=_ref(), label="l1"), s,
                   on_draft_intent=lambda d: 1 / 0, on_draft_appended=lambda d: 1 / 0) is None
