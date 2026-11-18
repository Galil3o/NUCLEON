"""R-011: size/count budgets; overruns are visible per message and never abort the run."""
import base64
import io
import json

import pytest
from pydantic import ValidationError

from nucleon import cli
from nucleon.budgets import BudgetEvent, BudgetExceeded
from nucleon.config import Config
from nucleon.engine import RunReport, run_once
from nucleon.journal import NullJournal
from nucleon.mailstore import BodyFetch
from nucleon.mailstore.fake import FakeMailStore
from nucleon.mailstore.imap import ImapMailStore
from nucleon.marker import FileMarkerStore, ProcessingMarker
from nucleon.models import Classification, MessageMeta, MessageRef
from nucleon.quarantine.fake import FakeClassifier
from nucleon.quarantine.llm import LLMClassifier, urllib_post

HDR = b"Subject: Hello\r\nFrom: al@x.com\r\nMessage-ID: <m1@x.com>\r\n\r\n"
TEXT_PART = '("text" "plain" ("charset" "utf-8") NIL NIL "7bit" %d 1)'


class Conn:
    """IMAP-shaped fake with a configurable size/structure/part payload."""

    def __init__(self, size=100, structure=None, parts=None):
        self.size = size
        self.structure = structure or (TEXT_PART % 11)
        self.parts = parts if parts is not None else {"1": b"hello world"}
        self.fetches = []

    def select(self, mailbox, readonly=False):
        return "OK", [b"3"]

    def response(self, code):
        return code, [b"77"]

    def uid(self, command, *args):
        if command == "SEARCH":
            return "OK", [b"5"]
        if command == "FETCH":
            item = args[1]
            self.fetches.append(item)
            if "BODYSTRUCTURE" in item:
                line = f"1 (UID 5 RFC822.SIZE {self.size} BODYSTRUCTURE {self.structure})"
                return "OK", [line.encode()]
            if item.startswith("(BODY.PEEK[HEADER]"):
                return "OK", [(b"1 (UID 5 BODY[HEADER] {%d}" % len(HDR), HDR), b")"]
            spec = item[len("(BODY.PEEK["):item.index("]")]
            data = self.parts[spec]
            if "<0." in item:
                n = int(item[item.index("<0.") + 3:item.index(">")])
                data = data[:n]
            return "OK", [(b"1 (UID 5 BODY[%s] {%d}" % (spec.encode(), len(data)), data), b")"]
        return "OK", [b""]


def _store(conn, **kw):
    return ImapMailStore(conn, "acct", **kw)


def _ref(store):
    return store.list_window("INBOX", 7, 1)[0]


# --- IMAP: check size/structure BEFORE loading; fetch only needed parts, byte-limited
def test_imap_oversize_message_rejected_before_any_body_fetch():
    conn = Conn(size=50_000_000)
    s = _store(conn, max_message_bytes=1_000_000)
    ref = _ref(s)
    conn.fetches.clear()
    with pytest.raises(BudgetExceeded) as e:
        s.fetch_body(ref)
    assert e.value.kind == "message_size" and e.value.observed == 50_000_000
    assert len(conn.fetches) == 1 and "RFC822.SIZE" in conn.fetches[0]
    assert not any("BODY.PEEK[1" in f or "BODY.PEEK[]" in f for f in conn.fetches)


def test_imap_never_fetches_the_whole_message_and_limits_bytes():
    conn = Conn(size=900, structure=TEXT_PART % 11)
    s = _store(conn, max_body_bytes=4096)
    ref = _ref(s)
    conn.fetches.clear()
    got = s.fetch_body(ref)
    assert got == BodyFetch(text="hello world", truncated=False, original_bytes=11)
    assert "RFC822.SIZE" in conn.fetches[0] and "BODYSTRUCTURE" in conn.fetches[0]
    assert conn.fetches[1] == "(BODY.PEEK[1]<0.4096>)"
    assert all("BODY.PEEK[]" not in f for f in conn.fetches)


def test_imap_only_the_text_part_is_fetched_not_attachments():
    structure = ('(' + TEXT_PART % 11 +
                 '("application" "pdf" ("name" "a.pdf") NIL NIL "base64" 9000000 NIL '
                 '("attachment" ("filename" "a.pdf"))) "mixed")')
    conn = Conn(size=9_000_500, structure=structure,
                parts={"1": b"hello world", "2": b"X" * 10})
    s = _store(conn, max_message_bytes=10_000_000)
    got = s.fetch_body(_ref(s))
    assert got.text == "hello world"
    assert not any("BODY.PEEK[2" in f for f in conn.fetches)


def test_imap_attachment_text_part_is_skipped_for_the_real_body():
    att = '("text" "plain" NIL NIL NIL "7bit" 5 1 NIL ("attachment" ("filename" "n.txt")))'
    structure = f'({att}{TEXT_PART % 11} "mixed")'
    conn = Conn(size=500, structure=structure, parts={"1": b"NOTES", "2": b"hello world"})
    s = _store(conn)
    assert s.fetch_body(_ref(s)).text == "hello world"


def test_imap_large_text_part_is_truncated_visibly():
    conn = Conn(size=2_000_000, structure=TEXT_PART % 1_000_000, parts={"1": b"A" * 1_000_000})
    s = _store(conn, max_body_bytes=1000)
    got = s.fetch_body(_ref(s))
    assert got.truncated is True and got.original_bytes == 1_000_000
    assert len(got.text.encode()) <= 1000
    assert conn.fetches[-1] == "(BODY.PEEK[1]<0.1000>)"


def test_imap_decodes_base64_and_qp_with_charset():
    b64 = '("text" "plain" ("charset" "utf-8") NIL NIL "base64" 20 1)'
    payload = base64.b64encode("Grüße".encode())
    conn = Conn(structure=b64, parts={"1": payload})
    s = _store(conn)
    assert s.fetch_body(_ref(s)).text == "Grüße"
    qp = '("text" "plain" ("charset" "iso-8859-1") NIL NIL "quoted-printable" 9 1)'
    conn = Conn(structure=qp, parts={"1": b"Gr=FC=DFe"})
    s = _store(conn)
    assert s.fetch_body(_ref(s)).text == "Grüße"


def test_imap_truncated_base64_still_decodes():
    b64 = '("text" "plain" ("charset" "utf-8") NIL NIL "base64" 400 1)'
    payload = base64.b64encode(b"x" * 300)
    conn = Conn(size=900, structure=b64, parts={"1": payload})
    s = _store(conn, max_body_bytes=101)  # cut mid-quantum
    got = s.fetch_body(_ref(s))
    assert got.truncated and set(got.text) == {"x"}


def test_imap_unparseable_structure_fails_closed():
    conn = Conn(structure="((((")
    s = _store(conn)
    ref = _ref(s)
    conn.fetches.clear()
    with pytest.raises(ValueError):
        s.fetch_body(ref)
    assert not any("BODY.PEEK[1" in f for f in conn.fetches)


def test_imap_no_text_part_gives_empty_body_without_part_fetch():
    conn = Conn(structure='("application" "pdf" NIL NIL NIL "base64" 10)')
    s = _store(conn)
    ref = _ref(s)
    conn.fetches.clear()
    assert s.fetch_body(ref).text == ""
    assert len(conn.fetches) == 1


def test_imap_headers_are_byte_limited_too():
    conn = Conn()
    s = _store(conn, max_header_bytes=2048)
    _ref(s)
    assert any(f == "(BODY.PEEK[HEADER]<0.2048>)" for f in conn.fetches)


# --- Fake store mirrors the contract -------------------------------------------
def _meta(uid=1) -> MessageMeta:
    ref = MessageRef.from_source("a", "INBOX", 1, uid, f"<in{uid}@x>", b"raw%d" % uid)
    return MessageMeta(ref=ref, subject="Hello", from_addr="a@b.c", to_addrs=["d@e.f"],
                       message_id=f"<in{uid}@x>")


def test_fake_store_rejects_oversize_and_truncates_body():
    s = FakeMailStore(max_message_bytes=100, max_body_bytes=10)
    small, big = _meta(1), _meta(2)
    s.seed_inbox(small, "x" * 50)
    s.seed_inbox(big, "x" * 500)
    got = s.fetch_body(small.ref)
    assert got.truncated and len(got.text) == 10 and got.original_bytes == 50
    with pytest.raises(BudgetExceeded):
        s.fetch_body(big.ref)


# --- Config -------------------------------------------------------------------
def _cfg(**limits) -> Config:
    return Config.model_validate({
        "mail": {"mode": "imap", "host": "h", "port": 993, "username": "u", "id": "acct-1"},
        "classifier": {"endpoint": "http://127.0.0.1:1", "model": "m"},
        "limits": limits,
    })


def test_limits_have_defaults_and_reject_nonpositive():
    lim = _cfg().limits
    for f in ("max_message_bytes", "max_body_bytes", "max_header_bytes", "max_model_response_bytes",
              "max_model_input_chars", "max_labels_per_message", "max_actions_per_message"):
        assert getattr(lim, f) > 0
    with pytest.raises(ValidationError):
        _cfg(max_body_bytes=0)
    with pytest.raises(ValidationError):
        _cfg(bogus=1)


# --- Model HTTP response / input -------------------------------------------------
GOOD = json.dumps({"category": "c", "labels": [], "urgency": "low", "needs_reply": False,
                   "draft_subject": None, "draft_body": None})


def _mmeta():
    return _meta()


def test_oversize_model_content_is_rejected():
    big = {"choices": [{"message": {"content": "x" * 5000}}]}
    clf = LLMClassifier("http://127.0.0.1:1", "m", http_post=lambda u, p: big,
                        max_response_bytes=1000)
    with pytest.raises(BudgetExceeded) as e:
        clf.classify(_mmeta(), "b")
    assert e.value.kind == "model_response" and e.value.limit == 1000


class _Resp(io.BytesIO):
    def __init__(self, data, length=None):
        super().__init__(data)
        self.headers = {"Content-Length": str(length)} if length is not None else {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_http_transport_reads_at_most_limit_plus_one(monkeypatch):
    reads = []
    resp = _Resp(b"{" + b" " * 10_000 + b"}")
    real_read = resp.read

    def spy(n=-1):
        reads.append(n)
        return real_read(n)

    resp.read = spy  # type: ignore[method-assign]
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=0: resp)
    with pytest.raises(BudgetExceeded) as e:
        urllib_post("http://127.0.0.1:1/x", {}, max_bytes=100)
    assert e.value.kind == "model_response" and reads == [101]


def test_http_transport_rejects_by_content_length_without_reading(monkeypatch):
    resp = _Resp(b"{}", length=10_000_000)
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=0: resp)
    with pytest.raises(BudgetExceeded) as e:
        urllib_post("http://127.0.0.1:1/x", {}, max_bytes=100)
    assert e.value.observed == 10_000_000 and resp.tell() == 0


def test_http_transport_small_response_ok(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=0: _Resp(b'{"a": 1}'))
    assert urllib_post("http://127.0.0.1:1/x", {}, max_bytes=100) == {"a": 1}


# --- Engine: visible per message, run continues ----------------------------------
def _marker(tmp_path):
    return ProcessingMarker(FileMarkerStore(str(tmp_path / "m.jsonl")))


def _cls(**kw):
    base = dict(category="personal", labels=[], urgency="normal", needs_reply=False)
    base.update(kw)
    return Classification(**base)


def test_oversize_mail_is_visibly_rejected_and_the_run_continues(tmp_path):
    cfg = _cfg(max_message_bytes=100)
    store = FakeMailStore(max_message_bytes=100)
    big, ok = _meta(1), _meta(2)
    store.seed_inbox(big, "x" * 500)
    store.seed_inbox(ok, "short")
    clf = FakeClassifier(_cls(labels=["nucleon/l1"]))
    rep = run_once(store, clf, NullJournal(), cfg, _marker(tmp_path))
    assert rep.over_budget == 1 and rep.processed == 1 and rep.errors == 0
    (ev,) = rep.budget_events
    assert (ev.kind, ev.outcome, ev.limit, ev.observed) == ("message_size", "rejected", 100, 500)
    assert [r.uid for r, _ in store.labels] == [2]  # nothing done to the oversize mail
    # visible, hashes only: no content, no plain key
    assert "x" * 5 not in ev.model_dump_json() and "INBOX" not in ev.model_dump_json()
    again = run_once(store, clf, NullJournal(), cfg, _marker(tmp_path))
    assert again.processed == 0 and again.over_budget == 0  # no auto-retry, no duplicate work


def test_truncated_body_is_processed_but_flagged(tmp_path):
    cfg = _cfg(max_body_bytes=10)
    store = FakeMailStore(max_body_bytes=10)
    store.seed_inbox(_meta(), "y" * 40)
    rep = run_once(store, FakeClassifier(_cls()), NullJournal(), cfg, _marker(tmp_path))
    assert rep.processed == 1 and rep.over_budget == 0
    (ev,) = rep.budget_events
    assert (ev.kind, ev.outcome, ev.limit, ev.observed) == ("body_bytes", "truncated", 10, 40)


def test_model_input_is_bounded_and_flagged(tmp_path):
    seen = []

    class Spy:
        def classify(self, meta, body):
            seen.append(body)
            return _cls()

    store = FakeMailStore()
    store.seed_inbox(_meta(), "z" * 500)
    rep = run_once(store, Spy(), NullJournal(), _cfg(max_model_input_chars=50), _marker(tmp_path))
    assert len(seen[0]) == 50
    (ev,) = rep.budget_events
    assert (ev.kind, ev.outcome, ev.limit, ev.observed) == ("model_input", "truncated", 50, 500)


def test_oversize_model_response_rejects_that_message_only(tmp_path):
    class Flaky:
        n = 0

        def classify(self, meta, body):
            Flaky.n += 1
            if Flaky.n == 1:
                raise BudgetExceeded("model_response", 1000, 5000)
            return _cls(labels=["nucleon/l1"])

    store = FakeMailStore()
    store.seed_inbox(_meta(1), "a")
    store.seed_inbox(_meta(2), "b")
    rep = run_once(store, Flaky(), NullJournal(), _cfg(), _marker(tmp_path))
    assert rep.over_budget == 1 and rep.processed == 1 and rep.errors == 0
    assert rep.budget_events[0].kind == "model_response"
    assert rep.budget_events[0].outcome == "rejected"


def test_too_many_labels_are_capped_and_flagged(tmp_path):
    store = FakeMailStore()
    store.seed_inbox(_meta(), "b")
    labels = [f"nucleon/l{i}" for i in range(30)]
    rep = run_once(store, FakeClassifier(_cls(labels=labels)), NullJournal(),
                   _cfg(max_labels_per_message=3), _marker(tmp_path))
    assert [l for _, l in store.labels] == labels[:3] and rep.labeled == 3
    (ev,) = rep.budget_events
    assert (ev.kind, ev.outcome, ev.limit, ev.observed) == ("labels", "truncated", 3, 30)


def test_total_actions_are_capped_and_flagged(tmp_path):
    store = FakeMailStore()
    store.seed_inbox(_meta(), "b")
    clf = FakeClassifier(_cls(labels=["nucleon/a", "nucleon/b"], needs_reply=True,
                              draft_body="hi"))
    rep = run_once(store, clf, NullJournal(),
                   _cfg(max_actions_per_message=2, max_labels_per_message=5), _marker(tmp_path))
    assert len(store.labels) == 2 and store.appended_drafts == []
    assert any(e.kind == "actions" and e.limit == 2 and e.observed == 3 for e in rep.budget_events)


def test_within_budget_run_reports_nothing_extra(tmp_path):
    store = FakeMailStore()
    store.seed_inbox(_meta(), "b")
    rep = run_once(store, FakeClassifier(_cls(labels=["nucleon/l1"])), NullJournal(), _cfg(),
                   _marker(tmp_path))
    assert rep == RunReport(processed=1, labeled=1)


def test_cli_prints_budget_summary(tmp_path, capsys):
    p = tmp_path / "config.yaml"
    p.write_text('mail: {mode: imap, host: h, port: 993, username: u, id: acct-1}\n'
                 'classifier: {endpoint: "http://127.0.0.1:1", model: m}\n'
                 f"registry_path: {tmp_path / 'acc.json'}\n"
                 f"marker: {{path: {tmp_path / 'm.jsonl'}}}\nlimits: {{max_message_bytes: 100}}\n",
                 encoding="utf-8")
    store = FakeMailStore(max_message_bytes=100)
    store.seed_inbox(_meta(), "x" * 500)
    rc = cli.main(["run", "--config", str(p)],
                  deps=lambda cfg: (store, FakeClassifier(_cls()), NullJournal()))
    out = capsys.readouterr().out
    assert rc == 3  # R-014: a rejected message is a failed run and "over_budget=1" in out
    assert "budget: kind=message_size outcome=rejected limit=100 observed=500" in out


def test_budget_event_schema_has_no_content_fields():
    assert set(BudgetEvent.model_fields) == {"kind", "outcome", "limit", "observed", "ref_pseudonym"}
