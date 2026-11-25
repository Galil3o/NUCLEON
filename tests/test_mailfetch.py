import hashlib

import pytest
from email.message import EmailMessage

from nucleon.mailfetch.budget import DiskBudget, BudgetExceeded
from nucleon.mailfetch.batcher import Item, Batch, SkipOversize, plan_batches
from nucleon.mailfetch.parse import extract_with_payload
from nucleon.mailfetch.loop import MsgRef, process_folder


def _sha1(b): return hashlib.sha1(b).hexdigest()


def _eml(attachments):
    """attachments: list of (filename, data) -> raw bytes with an inline body + attachments."""
    m = EmailMessage()
    m["From"] = "a@b"; m["To"] = "c@d"; m["Subject"] = "x"
    m.set_content("body")
    for fn, data in attachments:
        m.add_attachment(data, maintype="application", subtype="octet-stream", filename=fn)
    return m.as_bytes()


# ---------- budget ----------

def test_budget_acquire_release_and_available():
    b = DiskBudget(cap_bytes=100)
    assert b.available == 100 and b.fits(100) and not b.fits(101)
    b.acquire(60); assert b.in_use == 60 and b.available == 40
    b.release(60); assert b.in_use == 0 and b.available == 100


def test_budget_exceeded_raises_and_leaves_state():
    b = DiskBudget(cap_bytes=50); b.acquire(40)
    with pytest.raises(BudgetExceeded):
        b.acquire(20)
    assert b.in_use == 40  # unchanged after the failed acquire


def test_budget_too_large_ever():
    b = DiskBudget(cap_bytes=50)
    assert b.too_large_ever(51) and not b.too_large_ever(50)


def test_budget_rejects_bad_cap():
    with pytest.raises(ValueError):
        DiskBudget(cap_bytes=0)


# ---------- batcher ----------

def test_plan_batches_groups_under_cap_in_order():
    items = [Item("a", 30), Item("b", 30), Item("c", 30)]
    out = list(plan_batches(items, cap_bytes=70))
    assert [type(x) for x in out] == [Batch, Batch]
    assert [i.id for i in out[0].items] == ["a", "b"] and out[0].total == 60
    assert [i.id for i in out[1].items] == ["c"]


def test_plan_batches_skips_oversize_and_flushes_current():
    items = [Item("a", 30), Item("big", 999), Item("b", 10)]
    out = list(plan_batches(items, cap_bytes=70))
    assert isinstance(out[0], Batch) and [i.id for i in out[0].items] == ["a"]
    assert isinstance(out[1], SkipOversize) and out[1].item.id == "big"
    assert isinstance(out[2], Batch) and [i.id for i in out[2].items] == ["b"]


# ---------- parse ----------

def test_extract_with_payload_keeps_bytes_and_hash():
    data = b"%PDF-xyz"
    [blob] = extract_with_payload(_eml([("doc.pdf", data)]))
    assert blob.filename == "doc.pdf" and blob.data == data
    assert blob.sha1 == _sha1(data) and blob.size == len(data)


# ---------- loop (mocked effects) ----------

class FakeFetcher:
    def __init__(self, msgs):  # msgs: dict uid -> raw bytes
        self._m = msgs
    def list_messages(self, folder):
        return [MsgRef(uid, len(raw)) for uid, raw in self._m.items()]
    def fetch_raw(self, folder, uid):
        return self._m[uid]


class FakeStager:
    def __init__(self, fail_on=()):
        self.staged = []; self._fail = set(fail_on)
    def stage(self, filename, data, sha1):
        if sha1 in self._fail:
            raise OSError("stage failed")
        self.staged.append((filename, sha1)); return "/inbox/" + filename


class FakeDeleter:
    def __init__(self): self.trashed = []
    def trash(self, folder, uid): self.trashed.append(uid)


class Lookup:
    """present: sha1 in mapping; else absent unless digestless -> unknown."""
    def __init__(self, present=(), digestless=False):
        self._present = set(present); self._dl = digestless
    def contains_sha1(self, sha1, under=None):
        return ["/Drive/x"] if sha1 in self._present else []
    def has_digestless(self, under=None):
        return self._dl


def _run(msgs, lookup, stager=None, deleter=None, cap=10_000_000, do_delete=True):
    stager = stager or FakeStager()
    deleter = deleter or FakeDeleter()
    rep = process_folder("INBOX", FakeFetcher(msgs), lookup, stager, deleter,
                         DiskBudget(cap_bytes=cap), do_delete=do_delete)
    return rep, stager, deleter


def test_absent_attachment_is_staged_and_source_trashed():
    data = b"new-doc"
    msgs = {"1": _eml([("a.pdf", data)])}
    rep, stager, deleter = _run(msgs, Lookup(present=(), digestless=False))
    assert rep.staged == 1 and (stager.staged[0][1] == _sha1(data))
    assert rep.deleted == 1 and deleter.trashed == ["1"]


def test_present_attachment_not_staged_but_source_trashed():
    data = b"already-there"
    msgs = {"1": _eml([("a.pdf", data)])}
    rep, stager, deleter = _run(msgs, Lookup(present=[_sha1(data)]))
    assert rep.staged == 0 and rep.already_present == 1
    assert rep.deleted == 1 and deleter.trashed == ["1"]


def test_unknown_attachment_keeps_source():
    data = b"maybe-there"
    msgs = {"1": _eml([("a.pdf", data)])}
    # not present AND scope has digestless files -> 'unknown'
    rep, stager, deleter = _run(msgs, Lookup(present=(), digestless=True))
    assert rep.staged == 0 and rep.kept_unknown == 1
    assert rep.deleted == 0 and deleter.trashed == []


def test_no_attachment_message_is_kept():
    msgs = {"1": _eml([])}
    rep, stager, deleter = _run(msgs, Lookup())
    assert rep.kept_no_attachments == 1 and rep.deleted == 0 and deleter.trashed == []


def test_staging_error_keeps_source():
    data = b"will-fail"
    msgs = {"1": _eml([("a.pdf", data)])}
    rep, stager, deleter = _run(msgs, Lookup(present=(), digestless=False),
                                stager=FakeStager(fail_on=[_sha1(data)]))
    assert rep.errors == 1 and rep.deleted == 0 and deleter.trashed == []


def test_do_delete_false_never_trashes():
    data = b"new-doc"
    msgs = {"1": _eml([("a.pdf", data)])}
    rep, stager, deleter = _run(msgs, Lookup(present=(), digestless=False), do_delete=False)
    assert rep.staged == 1 and rep.deleted == 0 and deleter.trashed == []


def test_oversize_message_skipped_not_fetched_to_budget():
    big = _eml([("big.bin", b"Z" * 500)])
    msgs = {"1": big}
    rep, stager, deleter = _run(msgs, Lookup(), cap=100)  # message far exceeds cap
    assert rep.skipped_oversize == 1 and rep.processed == 0 and rep.staged == 0
    assert rep.deleted == 0
