import hashlib
import os

import pytest
from email.message import EmailMessage

from nucleon.pipeline.abdruck import ChromiumAbdruck, message_to_html
from nucleon.pipeline.archive import run_archive
from nucleon.filing.router import Router
from nucleon.filing.stager import RoutingStager
from nucleon.mailfetch.loop import MsgRef
from nucleon.mailfetch.budget import DiskBudget

INBOX_FOLDER = "/my-files/0 NUCLEON INBOX"


def _sha1(b): return hashlib.sha1(b).hexdigest()


def _eml(fn, data, *, subject="s", html_body=None):
    m = EmailMessage()
    m["From"] = "a@b"; m["To"] = "c@d"; m["Subject"] = subject
    if html_body is not None:
        m.set_content("plain fallback")
        m.add_alternative(html_body, subtype="html")
    else:
        m.set_content("body text")
    if fn is not None:
        m.add_attachment(data, maintype="application", subtype="pdf", filename=fn)
    return m.as_bytes()


# ---------- message_to_html ----------

def test_html_record_has_headers_and_escaped_plain_body():
    raw = _eml(None, b"", subject="Re: <script>")
    out = message_to_html(raw)
    assert "From" in out and "a@b" in out
    assert "&lt;script&gt;" in out          # subject is escaped, not injected
    assert "<pre>" in out and "body text" in out


def test_html_record_prefers_html_part_verbatim():
    raw = _eml(None, b"", html_body="<p id='x'>hello</p>")
    out = message_to_html(raw)
    assert "<p id='x'>hello</p>" in out       # html part rendered as-is


# ---------- ChromiumAbdruck (mocked runner) ----------

class _Res:
    def __init__(self, rc=0, stderr=""): self.returncode = rc; self.stderr = stderr


def test_abdruck_builds_print_to_pdf_command(tmp_path):
    seen = {}
    def fake_run(argv, **kw):
        seen["argv"] = argv
        assert os.path.exists(argv[-1].replace("file://", ""))   # input html exists at call
        return _Res(0)
    ab = ChromiumAbdruck(out_dir=str(tmp_path), chromium_bin="/bin/chrome", runner=fake_run)
    out = ab.record("INBOX", "42", _eml(None, b""))
    argv = seen["argv"]
    assert argv[0] == "/bin/chrome" and "--headless" in argv and "--no-sandbox" in argv
    assert any(a.startswith("--user-data-dir=") for a in argv)   # R-035a: isolated profile
    ptp = [a for a in argv if a.startswith("--print-to-pdf=")]
    assert len(ptp) == 1 and ptp[0] == "--print-to-pdf=" + out + ".tmp"   # temp in out_dir, no EXDEV
    base = os.path.basename(out)
    assert base.startswith("INBOX__42-") and base.endswith(".pdf")   # R-034: folder+uid+keyhash
    assert os.path.dirname(out) == str(tmp_path)


def test_abdruck_composite_key_avoids_uid_collision(tmp_path):
    # R-034: the same UID in two different folders must NOT map to the same PDF.
    ab = ChromiumAbdruck(out_dir=str(tmp_path), runner=lambda *a, **k: _Res(0))
    a = ab.record("INBOX", "7", _eml(None, b""))
    b = ab.record("Archive", "7", _eml(None, b""))
    assert a != b
    assert os.path.basename(a).startswith("INBOX__7-") and os.path.basename(b).startswith("Archive__7-")


def test_abdruck_key_prefix_namespaces_account(tmp_path):
    # R-034: the live adapter injects account/UIDVALIDITY via key_prefix.
    ab = ChromiumAbdruck(out_dir=str(tmp_path), key_prefix="acct9-uv42", runner=lambda *a, **k: _Res(0))
    out = ab.record("INBOX", "7", _eml(None, b""))
    assert os.path.basename(out).startswith("acct9-uv42__INBOX__7-")


def test_abdruck_record_is_deterministic(tmp_path):
    ab = ChromiumAbdruck(out_dir=str(tmp_path), runner=lambda *a, **k: _Res(0))
    assert ab.record("INBOX", "7", _eml(None, b"")) == ab.record("INBOX", "7", _eml(None, b""))


def test_html_record_blocks_scripts_and_external_fetches():
    out = message_to_html(_eml(None, b"", html_body="<p>hi</p>"))
    assert "Content-Security-Policy" in out and "default-src 'none'" in out


def test_html_record_strips_meta_refresh_and_scripts():
    body = "<meta http-equiv='refresh' content='0;url=http://evil'><script>x()</script><p>ok</p>"
    out = message_to_html(_eml(None, b"", html_body=body))
    # the message's own <meta>/<script> are stripped (only the document's CSP <meta> remains)
    assert "http-equiv='refresh'" not in out and "evil" not in out
    assert "<script>" not in out and "x()" not in out and "<p>ok</p>" in out


def test_abdruck_raises_on_nonzero_rc(tmp_path):
    ab = ChromiumAbdruck(out_dir=str(tmp_path), runner=lambda *a, **k: _Res(1, "boom"))
    with pytest.raises(OSError):
        ab.record("INBOX", "1", _eml(None, b""))


def test_abdruck_sanitizes_uid_into_filename(tmp_path):
    ab = ChromiumAbdruck(out_dir=str(tmp_path), runner=lambda *a, **k: _Res(0))
    out = ab.record("INBOX", "../../etc/passwd", _eml(None, b""))
    assert os.path.dirname(out) == str(tmp_path)   # never escapes out_dir
    assert "/" not in os.path.basename(out)[:-4]


# ---------- fakes for the loop ----------

class _Fetcher:
    def __init__(self, msgs): self._m = msgs
    def list_messages(self, folder): return [MsgRef(u, len(r)) for u, r in self._m.get(folder, {}).items()]
    def fetch_raw(self, folder, uid): return self._m[folder][uid]


class _AbsentLookup:
    def contains_sha1(self, sha1, under=None): return []
    def has_digestless(self, under=None): return False


class _RecordingUploader:
    def __init__(self): self.calls = []
    def __call__(self, data, filename, parent): self.calls.append(parent); return parent + "/" + filename


class _OrderLog:
    """Deleter + Recorder sharing one event log, to prove Abdruck precedes trash."""
    def __init__(self): self.events = []
    def record(self, folder, uid, raw): self.events.append(("record", uid)); return "/rec/" + uid + ".pdf"
    def trash(self, folder, uid): self.events.append(("trash", uid))


class _FailingRecorder:
    def record(self, folder, uid, raw): raise OSError("no pdf")


def _stager():
    up = _RecordingUploader()
    return RoutingStager(Router(INBOX_FOLDER), up), up


# ---------- run_archive ----------

def test_archive_default_files_but_never_deletes():
    data = b"att-1"
    st, up = _stager()
    log = _OrderLog()
    rep = run_archive(["INBOX"], _Fetcher({"INBOX": {"1": _eml("a.pdf", data)}}),
                      _AbsentLookup(), st, DiskBudget(cap_bytes=10_000_000),
                      deleter=log, recorder=log)   # provided, but do_delete defaults False
    assert rep.staged == 1 and rep.deleted == 0
    assert log.events == []                         # nothing recorded or trashed
    assert up.calls == [INBOX_FOLDER]


def test_archive_delete_requires_recorder():
    st, _ = _stager()
    with pytest.raises(ValueError, match="recorder"):
        run_archive(["INBOX"], _Fetcher({"INBOX": {}}), _AbsentLookup(), st,
                    DiskBudget(cap_bytes=10_000), deleter=_OrderLog(), do_delete=True)


def test_archive_delete_requires_deleter():
    st, _ = _stager()
    with pytest.raises(ValueError, match="deleter"):
        run_archive(["INBOX"], _Fetcher({"INBOX": {}}), _AbsentLookup(), st,
                    DiskBudget(cap_bytes=10_000), recorder=_OrderLog(), do_delete=True)


def test_archive_records_abdruck_before_trash():
    data = b"att-2"
    st, _ = _stager()
    log = _OrderLog()
    rep = run_archive(["INBOX"], _Fetcher({"INBOX": {"7": _eml("a.pdf", data)}}),
                      _AbsentLookup(), st, DiskBudget(cap_bytes=10_000_000),
                      deleter=log, recorder=log, do_delete=True)
    assert rep.staged == 1 and rep.deleted == 1
    assert log.events == [("record", "7"), ("trash", "7")]   # Abdruck strictly before delete


def test_archive_failed_abdruck_keeps_source():
    data = b"att-3"
    st, _ = _stager()
    class _Del:
        def __init__(self): self.trashed = []
        def trash(self, folder, uid): self.trashed.append(uid)
    deleter = _Del()
    rep = run_archive(["INBOX"], _Fetcher({"INBOX": {"9": _eml("a.pdf", data)}}),
                      _AbsentLookup(), st, DiskBudget(cap_bytes=10_000_000),
                      deleter=deleter, recorder=_FailingRecorder(), do_delete=True)
    assert rep.deleted == 0 and deleter.trashed == []       # source never destroyed
    assert rep.kept_no_abdruck == 1 and rep.staged == 1     # attachment still filed


def test_archive_aggregates_multiple_folders():
    st, up = _stager()
    log = _OrderLog()
    msgs = {"INBOX": {"1": _eml("a.pdf", b"x")}, "Archive": {"2": _eml("b.pdf", b"y")}}
    rep = run_archive(["INBOX", "Archive"], _Fetcher(msgs), _AbsentLookup(), st,
                      DiskBudget(cap_bytes=10_000_000), deleter=log, recorder=log, do_delete=True)
    assert len(rep.folders) == 2
    assert rep.staged == 2 and rep.deleted == 2
