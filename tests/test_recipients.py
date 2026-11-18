"""R-009: model-proposed recipients resolve ONLY against account-bound, approved
contacts; unknown addresses are review-required; the banner mirrors the ACTUAL
final recipients."""
from email import message_from_bytes as _mfb
from email.policy import default as _pol
from email.utils import getaddresses

import pytest
from pydantic import ValidationError

from nucleon.accounts import account_hash
from nucleon.actions import execute
from nucleon.config import Config
from nucleon.engine import run_once
from nucleon.gate import decide
from nucleon.mailstore.fake import FakeMailStore
from nucleon.marker import FileMarkerStore, ProcessingMarker
from nucleon.models import Classification, MessageMeta, MessageRef, StageDraft
from nucleon.quarantine import ClassifierOutputError, parse_classification
from nucleon.quarantine.fake import FakeClassifier
from nucleon.recipients import ContactBook, FileLearnedStore, LearnedEntry, learn

def message_from_bytes(b):
    return _mfb(b, policy=_pol)


ACCT = "acct-1"


def _cfg(**rec) -> Config:
    return Config.model_validate({
        "mail": {"mode": "imap", "host": "h", "port": 993, "username": "me@own.example",
                 "id": ACCT},
        "classifier": {"endpoint": "http://127.0.0.1:1", "model": "m"},
        "recipients": rec,
    })


def _meta(**kw) -> MessageMeta:
    ref = MessageRef.from_source(account_hash(ACCT), "INBOX", 1, 1, "<in@x>", b"raw")
    base = dict(ref=ref, subject="Hello", from_addr="sender@ext.example",
                to_addrs=["me@own.example", "peer@ext.example"], message_id="<in@x>")
    base.update(kw)
    return MessageMeta(**base)


def _cls(**kw) -> Classification:
    base = dict(category="personal", labels=[], urgency="normal", needs_reply=True,
                draft_body="Thanks")
    base.update(kw)
    return Classification(**base)


def _draft(cfg, meta=None, cls=None, contacts=None) -> StageDraft:
    acts = decide(cls or _cls(), meta or _meta(), cfg, contacts=contacts)
    return next(a for a in acts if isinstance(a, StageDraft))


def _addrs(d: StageDraft, role: str) -> list[str]:
    return [r.address for r in d.recipients if r.role == role]


def _mime(d: StageDraft):
    class S:
        def append_draft(self, target, raw):
            self.raw = raw
            return MessageRef.from_source("f", target, 1, 1, "<x>", raw)

        def set_label(self, *a): pass
        def find_draft(self, *a): return []

    s = S()
    execute(d, s)
    return message_from_bytes(s.raw)


# --- default: sender-only ----------------------------------------------------
def test_default_is_sender_only_without_banner_and_ignores_proposals():
    cfg = _cfg(contacts=["boss@own.example"])
    d = _draft(cfg, cls=_cls(proposed_recipients=["boss@own.example", "x@evil.example"]))
    assert _addrs(d, "to") == ["sender@ext.example"] and _addrs(d, "cc") == []
    assert d.to == "sender@ext.example" and d.review_required == ()
    assert d.banner is None
    assert "⚠" not in _mime(d).get_payload()


def test_reply_to_is_honoured_with_provenance_and_triggers_the_banner():
    d = _draft(_cfg(), meta=_meta(reply_to="Other <reply@elsewhere.example>"))
    assert [(r.address, r.source) for r in d.recipients] == [
        ("reply@elsewhere.example", "reply-to")]
    assert d.banner == "de"  # final recipient differs from the sender


def test_reply_to_equal_to_from_is_no_difference():
    d = _draft(_cfg(), meta=_meta(reply_to="SENDER@ext.example"))
    assert d.banner is None and _addrs(d, "to") == ["sender@ext.example"]


# --- sender-and-recipients -----------------------------------------------------
def test_sender_and_recipients_excludes_own_addresses_and_flags_banner():
    cfg = _cfg(mode="sender-and-recipients", own_addresses=["alias@own.example"])
    meta = _meta(to_addrs=["Me <me@own.example>", "alias@own.example", "peer@ext.example"],
                 cc_addrs=["cc@ext.example", "PEER@ext.example"])
    d = _draft(cfg, meta=meta)
    assert _addrs(d, "to") == ["sender@ext.example"]
    assert _addrs(d, "cc") == ["peer@ext.example", "cc@ext.example"]
    assert {r.source for r in d.recipients if r.role == "cc"} == {"original-recipient"}
    assert d.banner == "de"


# --- model-decided: the boundary ------------------------------------------------
def test_model_proposals_resolve_only_against_configured_contacts():
    cfg = _cfg(mode="model-decided", contacts=["Boss@Own.example"])
    d = _draft(cfg, cls=_cls(proposed_recipients=[
        "boss@own.example", "Stranger <stranger@evil.example>", "BOSS@own.example"]))
    assert _addrs(d, "to") == ["sender@ext.example"]
    assert [(r.address, r.source) for r in d.recipients if r.role == "cc"] == [
        ("boss@own.example", "contact")]
    assert d.review_required == ("stranger@evil.example",)
    assert d.banner == "de"
    assert "stranger@evil.example" not in (d.to or "")


def test_unknown_address_is_never_in_the_final_headers():
    cfg = _cfg(mode="model-decided")  # no contacts at all
    d = _draft(cfg, cls=_cls(proposed_recipients=["new@evil.example"]))
    m = _mime(d)
    hdr = " ".join(str(m[h] or "") for h in ("To", "Cc", "Bcc"))
    assert "evil" not in hdr and "sender@ext.example" in hdr
    assert d.review_required == ("new@evil.example",)


@pytest.mark.parametrize("bad", [
    "a@b.example\nBcc: x@evil.example", "a@b.example, c@d.example", "no-at-sign",
    "a@b.example>", "", "x@y\r\nSubject: z", "a b@c.example", "<>",
])
def test_malformed_or_injected_proposals_never_reach_headers(bad):
    cfg = _cfg(mode="model-decided", contacts=["a@b.example", "c@d.example"])
    d = _draft(cfg, cls=_cls(proposed_recipients=[bad]))
    assert _addrs(d, "cc") == []
    m = _mime(d)
    assert m["Bcc"] is None and "evil" not in str(m["To"]) + str(m["Cc"])


def test_proposal_of_the_sender_or_an_own_address_adds_nothing():
    cfg = _cfg(mode="model-decided")
    d = _draft(cfg, cls=_cls(proposed_recipients=["sender@ext.example", "me@own.example"]))
    assert _addrs(d, "to") == ["sender@ext.example"] and _addrs(d, "cc") == []
    assert d.review_required == () and d.banner is None


def test_proposal_that_is_only_a_thread_participant_is_still_review_required():
    """Boundary is contacts ONLY: being on the thread does not approve an address."""
    cfg = _cfg(mode="model-decided")
    d = _draft(cfg, cls=_cls(proposed_recipients=["peer@ext.example"]))
    assert d.review_required == ("peer@ext.example",) and _addrs(d, "cc") == []


# --- learned contacts (opt-in, separate store, account-bound) --------------------
def _store(tmp_path):
    return FileLearnedStore(str(tmp_path / "learned.jsonl"))


def test_learned_addresses_are_off_by_default(tmp_path):
    st = _store(tmp_path)
    learn(st, ACCT, "seen@ext.example", origin="seen-in:abc123")
    cfg = _cfg(mode="model-decided")
    book = ContactBook.from_config(cfg, learned_store=st)
    d = _draft(cfg, cls=_cls(proposed_recipients=["seen@ext.example"]), contacts=book)
    assert d.review_required == ("seen@ext.example",) and _addrs(d, "cc") == []


def test_learned_addresses_when_enabled_carry_provenance(tmp_path):
    st = _store(tmp_path)
    learn(st, ACCT, "seen@ext.example", origin="seen-in:abc123")
    cfg = _cfg(mode="model-decided", learned_enabled=True)
    book = ContactBook.from_config(cfg, learned_store=st)
    d = _draft(cfg, cls=_cls(proposed_recipients=["seen@ext.example"]), contacts=book)
    r = next(r for r in d.recipients if r.role == "cc")
    assert (r.address, r.source, r.origin) == ("seen@ext.example", "learned", "seen-in:abc123")
    assert d.banner == "de"


def test_learned_addresses_of_another_account_are_invisible(tmp_path):
    st = _store(tmp_path)
    learn(st, "other-account", "seen@ext.example", origin="seen-in:zzz")
    cfg = _cfg(mode="model-decided", learned_enabled=True)
    book = ContactBook.from_config(cfg, learned_store=st)
    d = _draft(cfg, cls=_cls(proposed_recipients=["seen@ext.example"]), contacts=book)
    assert d.review_required == ("seen@ext.example",)


def test_learning_requires_an_origin_and_a_valid_address(tmp_path):
    st = _store(tmp_path)
    with pytest.raises(ValueError):
        learn(st, ACCT, "a@b.example", origin="")
    with pytest.raises(ValueError):
        learn(st, ACCT, "not an address", origin="seen-in:x")
    assert st.entries() == []


def test_learned_store_is_separate_from_marker_and_journal_and_tolerates_junk(tmp_path):
    st = _store(tmp_path)
    learn(st, ACCT, "a@b.example", origin="seen-in:1")
    with open(tmp_path / "learned.jsonl", "a") as f:
        f.write("{broken\n")
    assert [e.address for e in st.entries()] == ["a@b.example"]
    assert isinstance(st.entries()[0], LearnedEntry)


def test_gate_never_auto_learns_review_required_addresses(tmp_path):
    st = _store(tmp_path)
    cfg = _cfg(mode="model-decided", learned_enabled=True)
    book = ContactBook.from_config(cfg, learned_store=st)
    _draft(cfg, cls=_cls(proposed_recipients=["new@evil.example"]), contacts=book)
    assert st.entries() == []


# --- banner mirrors the ACTUAL final recipients ----------------------------------
def test_banner_lists_exactly_the_final_recipients_and_review_items():
    cfg = _cfg(mode="model-decided", contacts=["boss@own.example"])
    d = _draft(cfg, cls=_cls(proposed_recipients=["boss@own.example", "x@evil.example"]))
    m = _mime(d)
    final = {a for _, a in getaddresses([str(m["To"]), str(m["Cc"])])}
    assert final == {"sender@ext.example", "boss@own.example"}
    plain = m.get_body(("plain",)).get_content()
    first, last = plain.splitlines()[0], plain.split("\n\n")[0].splitlines()[-1]
    assert first.startswith("⚠") and first.rstrip().endswith("⚠") and last.startswith("⚠")
    assert "VOR DEM SENDEN LÖSCHEN / DELETE BEFORE SENDING" in plain
    banner = plain.split("\n\n")[0]
    for a in final:
        assert a in banner
    assert "x@evil.example" in banner and "NICHT" in banner  # flagged, clearly not added
    assert plain.rstrip().endswith("Thanks")  # original body follows the banner block


def test_banner_html_alternative_is_red_on_yellow_and_escaped():
    cfg = _cfg(mode="model-decided", contacts=["boss@own.example"])
    d = _draft(cfg, cls=_cls(proposed_recipients=["a&b@evil.example"]))
    html = _mime(d).get_body(("html",)).get_content()
    assert "color:#c00000" in html.replace(" ", "") or "color: #c00000" in html
    assert "background" in html and "a&amp;b@evil.example" in html and "a&b@" not in html
    assert html.lstrip().startswith("<") and "Thanks" in html


def test_banner_can_be_switched_off_but_recipient_boundary_stays():
    cfg = _cfg(mode="model-decided", contacts=["boss@own.example"], banner=False)
    d = _draft(cfg, cls=_cls(proposed_recipients=["boss@own.example", "x@evil.example"]))
    assert d.banner is None and d.review_required == ("x@evil.example",)
    assert _mime(d).get_content_type() == "text/plain"
    assert "evil" not in str(_mime(d)["Cc"])


def test_banner_language_follows_operator_language():
    cfg = _cfg(mode="sender-and-recipients", banner_language="en")
    plain = _mime(_draft(cfg)).get_body(("plain",)).get_content()
    assert "DELETE BEFORE SENDING" in plain and "ACHTUNG" not in plain


# --- config ------------------------------------------------------------------------
def test_recipient_config_defaults_are_safe():
    r = _cfg().recipients
    assert (r.mode, r.contacts, r.learned_enabled, r.banner, r.banner_language) == (
        "sender-only", [], False, True, "de")


def test_contacts_are_validated_and_normalised():
    assert _cfg(contacts=[" Boss@OWN.example "]).recipients.contacts == ["boss@own.example"]
    with pytest.raises(ValidationError):
        _cfg(contacts=["not an address"])
    with pytest.raises(ValidationError):
        _cfg(mode="reply-to-everyone")


# --- wiring: classifier output & engine -------------------------------------------
def test_classifier_output_may_carry_proposed_recipients_strictly():
    raw = ('{"category":"c","labels":[],"urgency":"low","needs_reply":true,'
           '"draft_subject":null,"draft_body":"b","proposed_recipients":["a@b.example"]}')
    assert parse_classification(raw).proposed_recipients == ["a@b.example"]
    with pytest.raises(ClassifierOutputError):
        parse_classification(raw.replace('["a@b.example"]', '"a@b.example"'))


def test_engine_end_to_end_stages_cc_only_for_contacts(tmp_path):
    cfg = _cfg(mode="model-decided", contacts=["boss@own.example"])
    store = FakeMailStore()
    store.seed_inbox(_meta(), "body")
    clf = FakeClassifier(_cls(proposed_recipients=["boss@own.example", "x@evil.example"]))
    marker = ProcessingMarker(FileMarkerStore(str(tmp_path / "m.jsonl")))
    from nucleon.journal import NullJournal
    rep = run_once(store, clf, NullJournal(), cfg, marker)
    assert rep.drafted == 1 and rep.errors == 0
    m = message_from_bytes(store.appended_drafts[0][1])
    assert "boss@own.example" in str(m["Cc"]) and "evil" not in str(m["To"]) + str(m["Cc"])
    assert "x@evil.example" in m.get_body(("plain",)).get_content()  # flagged for review only


def test_cli_wires_contacts_and_learned_store_only_when_opted_in(tmp_path):
    from nucleon import cli
    from nucleon.journal import NullJournal

    learned = tmp_path / "learned.jsonl"
    learn(FileLearnedStore(str(learned)), ACCT, "seen@ext.example", origin="seen-in:1")

    def run(enabled: bool, sub: str) -> str:
        d = tmp_path / sub
        d.mkdir()
        p = d / "config.yaml"
        p.write_text(
            "mail: {mode: imap, host: h, port: 993, username: u, id: acct-1}\n"
            'classifier: {endpoint: "http://127.0.0.1:1", model: m}\n'
            f"registry_path: {d / 'acc.json'}\nmarker: {{path: {d / 'm.jsonl'}}}\n"
            f"recipients: {{mode: model-decided, contacts: [boss@own.example], "
            f"learned_enabled: {str(enabled).lower()}, learned_path: {learned}}}\n",
            encoding="utf-8")
        store = FakeMailStore()
        store.seed_inbox(_meta(), "body")
        clf = FakeClassifier(_cls(proposed_recipients=["seen@ext.example", "boss@own.example"]))
        assert cli.main(["run", "--config", str(p)],
                        deps=lambda cfg: (store, clf, NullJournal())) == 0
        return str(message_from_bytes(store.appended_drafts[0][1])["Cc"])

    assert run(False, "off") == "boss@own.example"
    assert run(True, "on") == "seen@ext.example, boss@own.example"


def test_imap_adapter_exposes_reply_to_and_cc():
    from nucleon.mailstore.imap import ImapMailStore
    raw = (b"From: A <a@x.example>\r\nReply-To: R <r@y.example>, z@y.example\r\n"
           b"To: b@x.example\r\nCc: C <c@x.example>, d@x.example\r\nMessage-ID: <m@x>\r\n\r\n")
    ref = MessageRef.from_source("h", "INBOX", 1, 1, "<m@x>", raw)
    meta = ImapMailStore(None, "h")._meta_from(ref, raw)
    assert meta.reply_to == "r@y.example" and meta.cc_addrs == ["c@x.example", "d@x.example"]
