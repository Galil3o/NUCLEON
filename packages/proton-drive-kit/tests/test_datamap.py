from proton_drive_kit.records import DriveRecord
from proton_drive_kit.datamap import DataMap


def rec(path, sha1, is_folder=False):
    return DriveRecord(
        uid="u:" + path, parent_uid=None, name=path.rsplit("/", 1)[-1], path=path,
        is_folder=is_folder, sha1=sha1, sha1_verified=False, size=1, mtime=None,
        media_type=None, shared_by_url=False,
    )


def test_presence_hit_returns_path():
    m = DataMap(); m.add(rec("/a/x.pdf", "S1"))
    assert m.contains_sha1("S1") == ["/a/x.pdf"]


def test_presence_miss_returns_empty():
    m = DataMap(); m.add(rec("/a/x.pdf", "S1"))
    assert m.contains_sha1("nope") == []


def test_empty_or_none_sha1_never_indexed():
    m = DataMap()
    m.add(rec("/a", None, is_folder=True))
    m.add(rec("/a/f", None))
    m.add(rec("/a/g", ""))
    assert len(m) == 0
    assert m.contains_sha1("") == []


def test_same_sha1_multiple_paths():
    m = DataMap(); m.add(rec("/a/1", "S")); m.add(rec("/b/2", "S"))
    assert m.paths_for_sha1("S") == ["/a/1", "/b/2"]


def test_under_scopes_by_prefix():
    m = DataMap(); m.add(rec("/a/x", "S")); m.add(rec("/b/y", "S"))
    assert m.contains_sha1("S", under="/a") == ["/a/x"]


def test_add_all_and_records():
    m = DataMap()
    m.add_all([rec("/a/1", "S"), rec("/a", None, is_folder=True)])
    assert len(m) == 1
    assert len(m.records()) == 2
