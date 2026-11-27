from proton_drive_kit.records import DriveRecord
from proton_drive_kit.datamap import DataMap
from proton_drive_kit.scanner import ScanIncomplete

S1 = "1" * 40          # well-formed 40-hex digests (real sha1s are always this shape)
S = "a" * 40


def rec(path, sha1, is_folder=False):
    return DriveRecord(
        uid="u:" + path, parent_uid=None, name=path.rsplit("/", 1)[-1], path=path,
        is_folder=is_folder, sha1=sha1, sha1_verified=False, size=1, mtime=None,
        media_type=None, shared_by_url=False,
    )


def test_presence_hit_returns_path():
    m = DataMap(); m.add(rec("/a/x.pdf", S1))
    assert m.contains_sha1(S1) == ["/a/x.pdf"]


def test_presence_miss_returns_empty():
    m = DataMap(); m.add(rec("/a/x.pdf", S1))
    assert m.contains_sha1("b" * 40) == []


def test_empty_or_none_sha1_never_indexed():
    m = DataMap()
    m.add(rec("/a", None, is_folder=True))
    m.add(rec("/a/f", None))
    m.add(rec("/a/g", ""))
    assert len(m) == 0
    assert m.contains_sha1("") == []


def test_same_sha1_multiple_paths():
    m = DataMap(); m.add(rec("/a/1", S)); m.add(rec("/b/2", S))
    assert m.paths_for_sha1(S) == ["/a/1", "/b/2"]


def test_under_scopes_by_prefix():
    m = DataMap(); m.add(rec("/a/x", S)); m.add(rec("/b/y", S))
    assert m.contains_sha1(S, under="/a") == ["/a/x"]


def test_add_all_and_records():
    m = DataMap()
    m.add_all([rec("/a/1", S), rec("/a", None, is_folder=True)])
    assert len(m) == 1
    assert len(m.records()) == 2


def test_sha1_lookup_is_case_insensitive():
    # A backend that reports sha1 in uppercase must still match a lowercase hexdigest()
    # (and vice versa) — otherwise a real hit reads as a false 'absent'.
    up = "AABBCCDDEEFF00112233445566778899AABBCCDD"
    m = DataMap(); m.add(rec("/x/a.pdf", up))
    assert m.contains_sha1(up.lower()) == ["/x/a.pdf"]
    m2 = DataMap(); m2.add(rec("/y/b.pdf", up.lower()))
    assert m2.contains_sha1(up) == ["/y/b.pdf"]


def test_format_invalid_sha1_not_indexed_and_counts_digestless():
    # A format-invalid provider digest must NOT be indexed, and must make the scope report as
    # digest-incomparable, so a hash miss stays 'unknown' (via has_digestless), never 'absent'.
    m = DataMap(); m.add(rec("/a/x.pdf", "NOTAVALIDHEXDIGEST"))
    assert len(m) == 0
    assert m.has_digestless() is True
    assert m.contains_sha1("a" * 40) == []


def test_add_all_captures_incomplete_scopes_for_coverage():
    # An un-scanned (ScanIncomplete) region must be recorded so a presence check can
    # report 'unknown' (not 'absent') for a miss inside it. Good records still index.
    m = DataMap()
    m.add_all([rec("/r/case/doc.pdf", S1),
               ScanIncomplete(path="/r/locked", uid="UL", reason="list_error")],
              covered="/")
    assert m.contains_sha1(S1) == ["/r/case/doc.pdf"]
    assert m.coverage_incomplete() is True                 # whole map (a region is incomplete)
    assert m.coverage_incomplete("/r/locked") is True       # the un-scanned folder itself
    assert m.coverage_incomplete("/r/locked/sub") is True    # a query inside the un-scanned region
    assert m.coverage_incomplete("/r/case") is False         # disjoint sibling: not forced unknown


def test_coverage_complete_when_covered_and_no_incomplete():
    m = DataMap(); m.add(rec("/a/x.pdf", S1)); m.declare_covered("/")
    assert m.coverage_incomplete() is False
    assert m.coverage_incomplete("/a") is False


def test_no_declared_coverage_means_everything_unknown():
    # BOFH: the map stores gaps but, without a declared covered scope, proves nothing — a
    # miss cannot be 'absent' because the map never claimed to have scanned there.
    m = DataMap(); m.add(rec("/a/x.pdf", S1))
    assert m.coverage_incomplete() is True
    assert m.coverage_incomplete("/a") is True


def test_query_outside_covered_scope_is_incomplete():
    m = DataMap()
    m.add_all([rec("/Root/x.pdf", S1)], covered="/Root")
    assert m.coverage_incomplete("/Root") is False        # positively scanned
    assert m.coverage_incomplete("/Root/sub") is False     # within the scanned scope
    assert m.coverage_incomplete("/Other") is True         # never scanned -> unknown
    assert m.coverage_incomplete(None) is True             # a /Root scan does not prove the whole tree
