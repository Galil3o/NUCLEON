import hashlib

import pytest
from email.message import EmailMessage

from nucleon.presence import Attachment, check_presence, extract_attachments


def _sha1(b: bytes) -> str:
    return hashlib.sha1(b).hexdigest()


class FakeLookup:
    """Minimal PresenceLookup: sha1 -> paths, with optional subtree scoping."""

    def __init__(self, mapping):
        self._m = mapping

    def contains_sha1(self, sha1, under=None):
        paths = self._m.get(sha1, [])
        if under is None:
            return list(paths)
        base = under.rstrip("/")
        return [p for p in paths if p == base or p.startswith(base + "/")]


def _mixed_message(attachments):
    """attachments: list of (maintype, subtype, filename, data)."""
    m = EmailMessage()
    m["From"] = "a@example.com"
    m["To"] = "b@example.com"
    m["Subject"] = "x"
    m.set_content("inline body text")  # inline body, not an attachment
    for maintype, subtype, filename, data in attachments:
        m.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
    return m.as_bytes()


def test_inline_body_is_not_an_attachment():
    assert extract_attachments(_mixed_message([])) == []


def test_single_attachment_name_and_content_sha1():
    data = b"%PDF-1.7 fake pdf bytes"
    atts = extract_attachments(_mixed_message([("application", "pdf", "Schriftsatz.pdf", data)]))
    assert len(atts) == 1
    assert atts[0].filename == "Schriftsatz.pdf"
    assert atts[0].sha1 == _sha1(data)
    assert atts[0].size == len(data)


def test_multiple_attachments():
    a, b = b"aaa", b"bbbb"
    atts = extract_attachments(_mixed_message(
        [("application", "pdf", "a.pdf", a), ("image", "png", "b.png", b)]))
    by = {x.filename: x for x in atts}
    assert set(by) == {"a.pdf", "b.png"}
    assert by["a.pdf"].sha1 == _sha1(a)
    assert by["b.png"].sha1 == _sha1(b)


def test_attachment_without_filename_but_disposition_counts():
    data = b"RAWDATA"
    raw = (b"From: a@b\r\nTo: c@d\r\nSubject: x\r\nMIME-Version: 1.0\r\n"
           b"Content-Type: application/octet-stream\r\n"
           b"Content-Disposition: attachment\r\n\r\n" + data)
    atts = extract_attachments(raw)
    assert len(atts) == 1
    assert atts[0].filename == ""
    assert atts[0].sha1 == _sha1(data)


def test_check_presence_hit_and_miss():
    present, missing = b"already-in-drive", b"not-in-drive"
    lookup = FakeLookup({_sha1(present): ["/CASES/Fall/x.pdf"]})
    atts = [Attachment("x.pdf", _sha1(present), len(present)),
            Attachment("y.pdf", _sha1(missing), len(missing))]
    res = check_presence(atts, lookup)
    assert res[0].present is True
    assert res[0].present_paths == ("/CASES/Fall/x.pdf",)
    assert res[1].present is False
    assert res[1].present_paths == ()


def test_check_presence_under_scopes_subtree():
    sha = _sha1(b"doc")
    lookup = FakeLookup({sha: ["/CASES/Fall/x.pdf", "/HR/x.pdf"]})
    res = check_presence([Attachment("x.pdf", sha, 3)], lookup, under="/CASES")
    assert res[0].present_paths == ("/CASES/Fall/x.pdf",)


class FakeLookupD(FakeLookup):
    """FakeLookup that also reports whether the scope has digest-less files."""

    def __init__(self, mapping, digestless=False):
        super().__init__(mapping)
        self._digestless = digestless

    def has_digestless(self, under=None):
        return self._digestless


def test_absent_without_digestless_is_definite_absent():
    [r] = check_presence([Attachment("x.pdf", _sha1(b"z"), 1)], FakeLookupD({}, digestless=False))
    assert r.status == "absent" and r.present is False


def test_absent_with_digestless_in_scope_is_unknown():
    [r] = check_presence([Attachment("x.pdf", _sha1(b"z"), 1)], FakeLookupD({}, digestless=True))
    assert r.status == "unknown" and r.present is False and r.present_paths == ()


def test_present_takes_precedence_over_digestless():
    sha = _sha1(b"z")
    [r] = check_presence([Attachment("x.pdf", sha, 1)],
                         FakeLookupD({sha: ["/CASES/a.pdf"]}, digestless=True))
    assert r.status == "present"


def test_kit_datamap_digestless_file_yields_unknown():
    datamap = pytest.importorskip("proton_drive_kit.datamap")
    records = pytest.importorskip("proton_drive_kit.records")
    dm = datamap.DataMap()
    dm.add(records.DriveRecord(
        uid="u", parent_uid=None, name="scan.pdf", path="/CASES/Fall/scan.pdf",
        is_folder=False, sha1=None, sha1_verified=False, size=10, mtime=None,
        media_type=None, shared_by_url=False))  # a Drive file with NO claimed sha1
    [r] = check_presence([Attachment("scan.pdf", _sha1(b"whatever"), 7)], dm, under="/CASES")
    assert r.status == "unknown"


def test_kit_datamap_satisfies_presence_lookup():
    datamap = pytest.importorskip("proton_drive_kit.datamap")
    records = pytest.importorskip("proton_drive_kit.records")
    data = b"legal-doc-bytes"
    sha = _sha1(data)
    dm = datamap.DataMap()
    dm.add(records.DriveRecord(
        uid="u", parent_uid=None, name="akte.pdf", path="/CASES/ArbG/akte.pdf",
        is_folder=False, sha1=sha, sha1_verified=False, size=len(data),
        mtime=None, media_type=None, shared_by_url=False))
    atts = extract_attachments(_mixed_message([("application", "pdf", "akte.pdf", data)]))
    res = check_presence(atts, dm, under="/CASES")
    assert len(res) == 1 and res[0].present
    assert res[0].present_paths == ("/CASES/ArbG/akte.pdf",)


def test_config_has_drive_presence_gate_off_by_default():
    from nucleon.config import Config

    drive = Config.model_fields["drive"].default_factory()
    assert drive.presence_check.enabled is False
    assert drive.presence_check.under is None


def test_missing_has_digestless_capability_yields_unknown():
    # R-021 edge (BOFH review of c402277): a lookup that cannot report digestless-
    # ness cannot prove the scope is fully digest-comparable, so a hash miss must be
    # 'unknown', never a definite 'absent'.
    [r] = check_presence([Attachment("x.pdf", _sha1(b"z"), 1)], FakeLookup({}))
    assert r.status == "unknown" and r.present is False and r.present_paths == ()


def test_attachment_without_own_digest_yields_unknown():
    # R-021 edge (BOFH review of c402277): with no attachment digest the content
    # cannot be compared at all, so the result is inconclusive ('unknown'), never
    # 'absent' — even when the lookup positively reports a digest-comparable scope.
    [r] = check_presence([Attachment("x.pdf", "", 0)], FakeLookupD({}, digestless=False))
    assert r.status == "unknown" and r.present is False and r.present_paths == ()


def test_malformed_attachment_sha1_yields_unknown():
    # R-021 evidence boundary (BOFH review of 2ee1377): a non-empty but non-SHA1 value
    # is not a comparable digest, so it must be 'unknown', never 'absent' — even with a
    # lookup that positively reports a digest-comparable scope.
    [r] = check_presence([Attachment("x.pdf", "not-a-sha1", 7)], FakeLookupD({}, digestless=False))
    assert r.status == "unknown" and r.present is False and r.present_paths == ()


def test_has_digestless_none_is_not_absence_proof():
    # R-021 evidence boundary (BOFH review of 2ee1377): has_digestless() returning None
    # is NOT positive proof of coverage; only an explicit False counts. A miss stays
    # 'unknown'.
    [r] = check_presence([Attachment("x.pdf", _sha1(b"z"), 1)], FakeLookupD({}, digestless=None))
    assert r.status == "unknown" and r.present is False


class FakeLookupC(FakeLookupD):
    """FakeLookupD that also reports whether the scanned coverage is incomplete."""

    def __init__(self, mapping, digestless=False, incomplete=False):
        super().__init__(mapping, digestless=digestless)
        self._incomplete = incomplete

    def coverage_incomplete(self, under=None):
        return self._incomplete


def test_absent_refused_when_coverage_incomplete():
    # R-021 (reviewer: unvollständige Teilbaumerfassung): the scope is digest-comparable
    # (has_digestless False) but a subtree was never scanned, so absence cannot be proven
    # -> 'unknown', never 'absent'.
    [r] = check_presence([Attachment("x.pdf", _sha1(b"z"), 1)],
                         FakeLookupC({}, digestless=False, incomplete=True))
    assert r.status == "unknown" and r.present is False and r.present_paths == ()


def test_absent_still_definite_when_coverage_complete():
    # Coverage proven complete AND digest-comparable AND no match -> definite absent.
    [r] = check_presence([Attachment("x.pdf", _sha1(b"z"), 1)],
                         FakeLookupC({}, digestless=False, incomplete=False))
    assert r.status == "absent"


def test_present_takes_precedence_over_incomplete_coverage():
    sha = _sha1(b"z")
    [r] = check_presence([Attachment("x.pdf", sha, 1)],
                         FakeLookupC({sha: ["/d/a.pdf"]}, digestless=False, incomplete=True))
    assert r.status == "present"


def test_kit_scan_incomplete_subtree_yields_unknown():
    # End-to-end honesty contract: scanner surfaces an un-scanned subtree, DataMap records
    # it, and a presence miss inside that subtree reads 'unknown', never a false 'absent'.
    scanner = pytest.importorskip("proton_drive_kit.scanner")
    datamap = pytest.importorskip("proton_drive_kit.datamap")
    records = pytest.importorskip("proton_drive_kit.records")
    from proton_drive_kit.access import DriveAccessError

    def _dir(path, uid):
        return records.DriveRecord(
            uid=uid, parent_uid=None, name=path.rsplit("/", 1)[-1], path=path,
            is_folder=True, sha1=None, sha1_verified=False, size=None, mtime=None,
            media_type=None, shared_by_url=False)

    class Acc:
        def list_dir(self, p):
            if p == "/CASES":
                return [_dir("/CASES/Locked", "ULOCK")]
            if p == "/CASES/Locked":
                raise DriveAccessError("boom")
            return []

    dm = datamap.DataMap()
    dm.add_all(scanner.scan(Acc(), "/CASES"), covered="/CASES")
    [r] = check_presence([Attachment("x.pdf", _sha1(b"z"), 1)], dm, under="/CASES/Locked")
    assert r.status == "unknown"


def test_presence_miss_outside_scanned_root_is_unknown():
    # BOFH: a successful empty scan under /Root must NOT make a miss in the never-scanned
    # /Other read 'absent' — the map declares its covered scope, and /Other is outside it.
    scanner = pytest.importorskip("proton_drive_kit.scanner")
    datamap = pytest.importorskip("proton_drive_kit.datamap")

    class Acc:
        def list_dir(self, p):
            return []   # /Root exists and is empty, listed successfully

    dm = datamap.DataMap()
    dm.add_all(scanner.scan(Acc(), "/Root"), covered="/Root")
    [out] = check_presence([Attachment("x.pdf", _sha1(b"z"), 1)], dm, under="/Other")
    assert out.status == "unknown"
    # a miss INSIDE the fully-scanned empty /Root is a definite absent
    [ins] = check_presence([Attachment("x.pdf", _sha1(b"z"), 1)], dm, under="/Root")
    assert ins.status == "absent"
