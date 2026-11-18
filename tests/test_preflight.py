"""R-010: IMAP preflight before the first write, and a missing APPENDUID handled
as its own case (resolve by the deterministic Message-ID, never trust uid 0)."""
from email import message_from_bytes

import pytest

from nucleon import cli
from nucleon.accounts import account_hash
from nucleon.actions import DraftLocationUnresolved, draft_message_id, execute, message_id_hash
from nucleon.config import Config
from nucleon.engine import run_once
from nucleon.journal import FileJournalStore, NullJournal, StoredJournal
from nucleon.mailstore import PreflightError, PreflightReport, PreflightRequest
from nucleon.mailstore.fake import FakeMailStore
from nucleon.mailstore.imap import ImapMailStore
from nucleon.marker import FileMarkerStore, ProcessingMarker
from nucleon.models import Classification, MessageMeta, MessageRef, StageDraft
from nucleon.quarantine.fake import FakeClassifier

ACCT = account_hash("acct-1")


def _cfg(**extra) -> Config:
    return Config.model_validate({
        "mail": {"mode": "imap", "host": "h", "port": 993, "username": "u", "id": "acct-1"},
        "classifier": {"endpoint": "http://127.0.0.1:1", "model": "m"},
        **extra,
    })


def _meta(uid=1, mid="<in@x>") -> MessageMeta:
    ref = MessageRef.from_source(ACCT, "INBOX", 1, uid, mid, b"raw%d" % uid)
    return MessageMeta(ref=ref, subject="Hello", from_addr="a@b.example", to_addrs=[],
                       message_id=mid)


def _cls(**kw) -> Classification:
    base = dict(category="c", labels=[], urgency="normal", needs_reply=True, draft_body="Thanks")
    base.update(kw)
    return Classification(**base)


def _marker(tmp_path) -> ProcessingMarker:
    return ProcessingMarker(FileMarkerStore(str(tmp_path / "m.jsonl")))


# =========================== missing APPENDUID ==================================
def _draft_action(label="nucleon/proposed") -> StageDraft:
    return StageDraft(ref=_meta().ref, subject="Re: Hello", body="Thanks", to="a@b.example",
                      target="outbox", label=label)


def test_missing_appenduid_resolves_draft_by_deterministic_message_id(tmp_path):
    store = FakeMailStore(append_returns_uid=False)
    store.seed_inbox(_meta(), "body")
    marker = _marker(tmp_path)
    j = StoredJournal(FileJournalStore(str(tmp_path / "j.jsonl")))
    rep = run_once(store, FakeClassifier(_cls()), j, _cfg(), marker)
    assert rep.drafted == 1 and rep.errors == 0
    # the label was applied to the RESOLVED draft, never to a uid-0 ref
    draft_labels = [(r, l) for r, l in store.labels if l == "nucleon/proposed"]
    assert len(draft_labels) == 1
    r = draft_labels[0][0]
    assert r.uid > 0 and r.uidvalidity > 0 and r.mailbox == "outbox"
    # marker/journal now hold a CONFIRMED stable reference
    e = marker.entry(_meta().ref.processing_key())
    assert e.status == "ok" and e.draft.confirmed and e.draft.uid == r.uid
    assert j._store.entries()[-1].draft_confirmed is True
    assert e.draft.message_id_hash == message_id_hash(draft_message_id(e.key))


def test_label_is_not_applied_before_the_draft_is_safely_located():
    order: list[str] = []

    class Spy(FakeMailStore):
        def find_draft(self, target, message_id):
            order.append("find")
            return super().find_draft(target, message_id)

        def set_label(self, ref, label):
            order.append("label")
            super().set_label(ref, label)

    store = Spy(append_returns_uid=False)
    execute(_draft_action(), store)
    assert order == ["find", "label"]


def test_appenduid_present_needs_no_lookup():
    class Spy(FakeMailStore):
        def find_draft(self, *a):
            raise AssertionError("must not search when APPENDUID was reported")

    execute(_draft_action(), Spy())


@pytest.mark.parametrize("copies", [0, 2])
def test_unresolvable_draft_is_an_own_error_and_never_gets_a_label(tmp_path, copies):
    class Dup(FakeMailStore):
        def find_draft(self, target, message_id):
            found = super().find_draft(target, message_id)
            return found[:0] if copies == 0 else found * 2

    store = Dup(append_returns_uid=False)
    store.seed_inbox(_meta(), "body")
    marker = _marker(tmp_path)
    rep = run_once(store, FakeClassifier(_cls()), NullJournal(), _cfg(), marker)
    assert rep.errors == 1 and rep.drafted == 0
    assert [l for _, l in store.labels] == []  # no follow-up operation on an unknown ref
    e = marker.entry(_meta().ref.processing_key())
    assert e.status == "error" and "StageDraft:appended" in e.steps
    assert e.draft is not None and e.draft.confirmed is False and e.draft.uid == 0
    # no automatic retry: a second run neither appends again nor re-labels
    run_once(store, FakeClassifier(_cls()), NullJournal(), _cfg(), marker)
    assert len(store.appended_drafts) == 1


def test_unresolved_error_type_is_distinct():
    class Gone(FakeMailStore):
        def find_draft(self, *a):
            return []

    with pytest.raises(DraftLocationUnresolved):
        execute(_draft_action(), Gone(append_returns_uid=False))


# ---- at the IMAP layer: a server without UIDPLUS/APPENDUID --------------------
class Conn:
    CAPS = b"IMAP4rev1 UIDPLUS MOVE NAMESPACE"
    LIST = [
        b'(\\HasNoChildren) "/" "INBOX"',
        b'(\\HasNoChildren \\Drafts) "/" "Drafts"',
        b'(\\HasNoChildren \\Trash) "/" "Trash"',
        b'(\\HasNoChildren) "/" "Nucleon/Outbox"',
        b'(\\HasNoChildren) "/" "nucleon/proposed"',
    ]
    NS = b'(("" "/")) NIL NIL'

    def __init__(self):
        self.cmds: list[tuple] = []
        self.appended: list[bytes] = []
        self.appenduid = False

    def capability(self):
        self.cmds.append(("CAPABILITY",))
        return "OK", [self.CAPS]

    def namespace(self):
        self.cmds.append(("NAMESPACE",))
        return "OK", [self.NS]

    def list(self, directory='""', pattern="*"):
        self.cmds.append(("LIST", directory, pattern))
        return "OK", list(self.LIST)

    def create(self, name):
        self.cmds.append(("CREATE", name))
        self.LIST = [*self.LIST, b'(\\HasNoChildren) "/" "%s"' % name.encode()]
        return "OK", [b"done"]

    def select(self, mailbox, readonly=False):
        self.cmds.append(("SELECT", mailbox, readonly))
        return "OK", [b"1"]

    def response(self, code):
        return code, [b"77"]

    def append(self, mailbox, flags, dt, message):
        self.cmds.append(("APPEND", mailbox))
        self.appended.append(message)
        return "OK", [b"[APPENDUID 77 42] done" if self.appenduid else b"Append completed"]

    def uid(self, command, *args):
        self.cmds.append(("UID", command) + args)
        if command == "SEARCH":
            return "OK", [b"7"] if self.appended else [b""]
        if command == "FETCH":
            raw = self.appended[-1]
            return "OK", [(b"1 (UID 7 BODY[] {%d}" % len(raw), raw), b")"]
        return "OK", [b"done"]


def test_imap_without_appenduid_resolves_via_search_and_labels_the_found_draft():
    conn = Conn()
    store = ImapMailStore(conn, ACCT)
    ref = execute(_draft_action(), store)
    assert (ref.mailbox, ref.uidvalidity, ref.uid) == ("Nucleon/Outbox", 77, 7)
    search = [c for c in conn.cmds if c[:2] == ("UID", "SEARCH")]
    mid = draft_message_id(_meta().ref.processing_key())
    assert search and search[-1][2:] == (None, "HEADER", "Message-ID", mid)
    copies = [c for c in conn.cmds if c[:2] == ("UID", "COPY")]
    assert copies and copies[0][2] == "7"  # the resolved uid, never 0


# =========================== preflight (IMAP) ===================================
def _req(**kw) -> PreflightRequest:
    base = dict(draft_targets=("outbox",), folders=("nucleon/proposed",), need_move=False)
    base.update(kw)
    return PreflightRequest(**base)


def test_preflight_ok_is_read_only():
    conn = Conn()
    rep = ImapMailStore(conn, ACCT).preflight(_req(need_move=True))
    assert rep.ok and rep.problems == () and rep.created == ()
    verbs = {c[0] for c in conn.cmds}
    assert verbs <= {"CAPABILITY", "NAMESPACE", "LIST"}  # no write, no SELECT even


def test_preflight_missing_move_capability_blocks_only_when_needed():
    conn = Conn()
    conn.CAPS = b"IMAP4rev1 UIDPLUS NAMESPACE"
    st = ImapMailStore(conn, ACCT)
    assert st.preflight(_req(need_move=False)).ok
    rep = st.preflight(_req(need_move=True))
    assert not rep.ok and any("MOVE" in p for p in rep.problems)


def test_preflight_without_uidplus_warns_but_does_not_block():
    conn = Conn()
    conn.CAPS = b"IMAP4rev1 MOVE"
    rep = ImapMailStore(conn, ACCT).preflight(_req())
    assert rep.ok and any("UIDPLUS" in w for w in rep.warnings)


def test_preflight_requires_imap4rev1():
    conn = Conn()
    conn.CAPS = b"UIDPLUS"
    rep = ImapMailStore(conn, ACCT).preflight(_req())
    assert not rep.ok and any("IMAP4rev1" in p for p in rep.problems)


def test_preflight_missing_folders_are_reported_not_created_by_default():
    conn = Conn()
    conn.LIST = [b'(\\HasNoChildren) "/" "INBOX"']
    rep = ImapMailStore(conn, ACCT).preflight(_req())
    assert not rep.ok
    text = " ".join(rep.problems)
    assert "Nucleon/Outbox" in text and "nucleon/proposed" in text
    assert not any(c[0] == "CREATE" for c in conn.cmds)


def test_preflight_creates_missing_folders_only_when_allowed_and_inside_namespace():
    conn = Conn()
    conn.LIST = [b'(\\HasNoChildren) "/" "INBOX"']
    rep = ImapMailStore(conn, ACCT).preflight(_req(create_missing=True))
    assert rep.ok and set(rep.created) == {"Nucleon/Outbox", "nucleon/proposed"}
    assert [c for c in conn.cmds if c[0] == "CREATE"]


def test_preflight_never_creates_system_folders():
    conn = Conn()
    conn.LIST = [b'(\\HasNoChildren) "/" "INBOX"']
    rep = ImapMailStore(conn, ACCT).preflight(
        _req(draft_targets=("drafts",), folders=("Trash",), create_missing=True))
    assert not rep.ok and not any(c[0] == "CREATE" for c in conn.cmds)


def test_preflight_namespace_prefix_and_delimiter_mismatch_is_a_problem():
    conn = Conn()  # Dovecot-style: personal namespace "INBOX." with "." delimiter
    conn.NS = b'(("INBOX." ".")) NIL NIL'
    conn.LIST = [b'(\\HasNoChildren) "." "INBOX"', b'(\\HasNoChildren \\Drafts) "." "INBOX.Drafts"']
    rep = ImapMailStore(conn, ACCT).preflight(_req(create_missing=True))
    assert not rep.ok
    assert any("delimiter" in p or "namespace" in p for p in rep.problems)
    assert not any(c[0] == "CREATE" for c in conn.cmds)  # never guesses a translation


def test_preflight_without_namespace_capability_uses_list_delimiter():
    conn = Conn()
    conn.CAPS = b"IMAP4rev1 UIDPLUS MOVE"
    assert ImapMailStore(conn, ACCT).preflight(_req()).ok
    assert not any(c[0] == "NAMESPACE" for c in conn.cmds)


def test_preflight_fails_closed_when_list_fails():
    conn = Conn()
    conn.list = lambda *a, **k: ("NO", [b"denied"])
    with pytest.raises(RuntimeError):
        ImapMailStore(conn, ACCT).preflight(_req())


# =========================== engine wiring ======================================
def test_engine_preflights_before_the_first_write_and_only_then(tmp_path):
    store = FakeMailStore()
    store.seed_inbox(_meta(), "body")
    run_once(store, FakeClassifier(_cls(labels=["nucleon/x"])), NullJournal(), _cfg(),
             _marker(tmp_path))
    (req,) = store.preflight_requests
    assert req.draft_targets == ("outbox",) and req.need_move is False
    assert set(req.folders) == {"nucleon/proposed", "nucleon/x"}
    # preflight happened before any mailbox write
    assert store.write_log[0] == "preflight"

    quiet = FakeMailStore()
    quiet.seed_inbox(_meta(uid=2, mid="<q@x>"), "body")
    run_once(quiet, FakeClassifier(_cls(needs_reply=False)), NullJournal(), _cfg(),
             _marker(tmp_path))
    assert quiet.preflight_requests == []  # nothing to write: no preflight needed


def test_engine_requests_move_capability_only_for_trash(tmp_path):
    store = FakeMailStore()
    store.seed_inbox(_meta(), "body")
    cfg = _cfg(cleanup={"auto_trash_enabled": True, "auto_trash_categories": ["c"]})
    run_once(store, FakeClassifier(_cls(needs_reply=False)), NullJournal(), cfg, _marker(tmp_path))
    assert store.preflight_requests[0].need_move is True


def test_engine_failed_preflight_aborts_before_any_write(tmp_path):
    store = FakeMailStore()
    store.preflight_problems = ("folder missing: Nucleon/Outbox",)
    store.seed_inbox(_meta(), "body")
    marker = _marker(tmp_path)
    with pytest.raises(PreflightError, match="Nucleon/Outbox"):
        run_once(store, FakeClassifier(_cls()), NullJournal(), _cfg(), marker)
    assert store.appended_drafts == [] and store.labels == []
    assert not marker.is_done(_meta().ref.processing_key())  # retry stays possible


def test_engine_passes_create_missing_from_config(tmp_path):
    store = FakeMailStore()
    store.seed_inbox(_meta(), "body")
    run_once(store, FakeClassifier(_cls()), NullJournal(),
             _cfg(draft={"create_missing_folders": True}), _marker(tmp_path))
    assert store.preflight_requests[0].create_missing is True
    store2 = FakeMailStore()
    store2.seed_inbox(_meta(), "body")
    run_once(store2, FakeClassifier(_cls()), NullJournal(), _cfg(), _marker(tmp_path / "x"))
    assert store2.preflight_requests[0].create_missing is False


def test_cli_reports_failed_preflight_as_handled_error(tmp_path, capsys):
    p = tmp_path / "config.yaml"
    p.write_text(
        "mail: {mode: imap, host: h, port: 993, username: u, id: acct-1}\n"
        'classifier: {endpoint: "http://127.0.0.1:1", model: m}\n'
        f"registry_path: {tmp_path / 'acc.json'}\nmarker: {{path: {tmp_path / 'm.jsonl'}}}\n",
        encoding="utf-8")
    store = FakeMailStore()
    store.preflight_problems = ("capability missing: MOVE",)
    store.seed_inbox(_meta(), "body")
    rc = cli.main(["run", "--config", str(p)],
                  deps=lambda cfg: (store, FakeClassifier(_cls()), NullJournal()))
    assert rc == 1 and "MOVE" in capsys.readouterr().err


def test_preflight_report_value_object():
    assert PreflightReport().ok
    assert not PreflightReport(problems=("x",)).ok
