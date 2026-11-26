import hashlib

import pytest
from email.message import EmailMessage

from nucleon.filing.router import Router, Rule
from nucleon.filing.stager import RoutingStager, ProtonDriveUploader
from nucleon.mailfetch.loop import MsgRef, process_folder
from nucleon.mailfetch.budget import DiskBudget

INBOX = "/my-files/0 NUCLEON INBOX"


def _sha1(b): return hashlib.sha1(b).hexdigest()


# ---------- Router ----------

def test_router_default_when_no_rules():
    r = Router(default_target=INBOX)
    assert r.target_for({"filename": "x.pdf"}) == INBOX


def test_router_disabled_rule_is_ignored():
    r = Router(INBOX, [Rule("legal", "/my-files/CASES/x", lambda m: True, enabled=False)])
    assert r.target_for({"filename": "x"}) == INBOX  # rule matches but is OFF


def test_router_enabled_rule_matches():
    r = Router(INBOX, [Rule("casematch", "/my-files/CASES/ExampleCase",
                            lambda m: "example" in m.get("filename", "").lower(), enabled=True)])
    assert r.target_for({"filename": "brief-example.docx"}) == "/my-files/CASES/ExampleCase"
    assert r.target_for({"filename": "other.pdf"}) == INBOX


def test_router_misbehaving_predicate_falls_back_to_default():
    def boom(m): raise RuntimeError("bad rule")
    r = Router(INBOX, [Rule("bad", "/my-files/WRONG", boom, enabled=True)])
    assert r.target_for({"filename": "x"}) == INBOX  # never diverts on a crashing rule


# ---------- RoutingStager ----------

class RecordingUploader:
    def __init__(self): self.calls = []
    def __call__(self, data, filename, parent):
        self.calls.append((filename, parent, _sha1(data))); return parent + "/" + filename


def test_routing_stager_uses_default_target():
    up = RecordingUploader()
    st = RoutingStager(Router(INBOX), up)
    path = st.stage("a.pdf", b"data", _sha1(b"data"))
    assert up.calls == [("a.pdf", INBOX, _sha1(b"data"))]
    assert path == INBOX + "/a.pdf"


def test_routing_stager_follows_enabled_rule():
    up = RecordingUploader()
    rule = Rule("inv", "/my-files/CORP", lambda m: m["filename"].endswith(".inv"), enabled=True)
    st = RoutingStager(Router(INBOX, [rule]), up)
    st.stage("x.inv", b"d", _sha1(b"d"))
    assert up.calls[0][1] == "/my-files/CORP"


# ---------- ProtonDriveUploader (command construction, mocked runner) ----------

class _Res:
    def __init__(self, rc=0, stderr=""): self.returncode = rc; self.stderr = stderr


def test_proton_uploader_builds_rename_command_and_returns_path():
    seen = {}
    def fake_run(argv, **kw):
        seen["argv"] = argv
        # the temp file must exist at call time
        assert argv[-2].endswith("/a.pdf")
        import os
        assert os.path.exists(argv[-2])
        return _Res(0)
    up = ProtonDriveUploader(bin_path="/bin/pd", runner=fake_run)
    path = up(b"hello", "a.pdf", INBOX)
    assert seen["argv"][:5] == ["/bin/pd", "fs", "upload", "-f", "rename"]
    assert seen["argv"][-1] == INBOX
    assert path == INBOX + "/a.pdf"


def test_proton_uploader_raises_on_nonzero_rc():
    up = ProtonDriveUploader(bin_path="/bin/pd", runner=lambda *a, **k: _Res(1, "boom"))
    with pytest.raises(OSError):
        up(b"x", "a.pdf", INBOX)


# ---------- integration with the mail loop ----------

def _eml(fn, data):
    m = EmailMessage(); m["From"] = "a@b"; m["To"] = "c@d"; m["Subject"] = "s"
    m.set_content("body"); m.add_attachment(data, maintype="application", subtype="pdf", filename=fn)
    return m.as_bytes()


class _Fetcher:
    def __init__(self, msgs): self._m = msgs
    def list_messages(self, folder): return [MsgRef(u, len(r)) for u, r in self._m.items()]
    def fetch_raw(self, folder, uid): return self._m[uid]


class _Lookup:
    def contains_sha1(self, sha1, under=None): return []       # absent
    def has_digestless(self, under=None): return False


def test_loop_with_routing_stager_lands_in_inbox():
    data = b"new-attachment"
    up = RecordingUploader()
    stager = RoutingStager(Router(INBOX), up)
    rep = process_folder("INBOX", _Fetcher({"1": _eml("a.pdf", data)}), _Lookup(),
                         stager, deleter=None, budget=DiskBudget(cap_bytes=10_000_000),
                         do_delete=False)
    assert rep.staged == 1
    assert up.calls == [("a.pdf", INBOX, _sha1(data))]
