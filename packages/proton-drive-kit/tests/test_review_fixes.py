import pytest

from proton_drive_kit.records import DriveRecord, parse_entry, parse_listing, ListingError
from proton_drive_kit.datamap import DataMap
from proton_drive_kit.access import DriveAccess, DriveAccessError

H = "a" * 40          # a well-formed 40-hex digest (format-valid, so it is indexed)


def _frec(path, sha1):
    return DriveRecord(uid="u", parent_uid=None, name=path.rsplit("/", 1)[-1], path=path,
                       is_folder=False, sha1=sha1, sha1_verified=False, size=1, mtime=None,
                       media_type=None, shared_by_url=False)


# Finding 1 — under= must scope by folder subtree, not raw string prefix.
def test_under_scope_does_not_leak_to_sibling_prefix():
    m = DataMap()
    m.add(_frec("/hr/Old/cv.pdf", H))
    m.add(_frec("/hr/Older/cv.pdf", H))
    assert m.contains_sha1(H, under="/hr/Old") == ["/hr/Old/cv.pdf"]


def test_under_scope_trailing_slash_is_equivalent():
    m = DataMap()
    m.add(_frec("/hr/Old/cv.pdf", H))
    m.add(_frec("/hr/Older/cv.pdf", H))
    assert m.contains_sha1(H, under="/hr/Old/") == ["/hr/Old/cv.pdf"]


# Finding 2 — a '/' in a name must not become a path separator (no spurious nesting).
def test_slash_in_name_is_neutralized_in_path_but_name_kept_raw():
    r = parse_entry({"name": {"value": "a/b"}, "type": "folder", "uid": "x"}, parent_path="/root")
    assert r.name == "a/b"                       # raw name preserved
    assert r.path == "/root/a∕b"            # the name's '/' neutralized in the path
    assert r.path[len("/root/"):].count("/") == 0  # leaf segment has no separator


# Finding 3 — a JSON array of non-objects is a bad listing, normalized to the kit's errors.
def test_parse_listing_rejects_list_of_non_objects():
    with pytest.raises(ListingError):
        parse_listing("[1, 2, 3]")


def test_access_wraps_list_of_non_objects_as_access_error():
    with pytest.raises(DriveAccessError):
        DriveAccess(runner=lambda p: "[1, 2, 3]").list_dir("/x")
