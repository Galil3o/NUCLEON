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


# R-019 — a single malformed entry (unexpected field types) must surface as ListingError,
# never crash the walk with an AttributeError (which would abort the whole scan on one bad
# subfolder, and — via the empty result — be misread as "folder empty" by the presence check).
def test_r019_numeric_name_is_listing_error_not_attributeerror():
    with pytest.raises(ListingError):
        parse_entry({"uid": "u", "name": 123, "type": "file"})


def test_r019_name_value_non_string_is_listing_error():
    with pytest.raises(ListingError):
        parse_entry({"uid": "u", "name": {"value": 5}, "type": "file"})


def test_r019_active_revision_as_array_is_listing_error():
    with pytest.raises(ListingError):
        parse_entry({"uid": "u", "name": "x", "type": "file", "activeRevision": [1, 2]})


def test_r019_claimed_digests_as_array_is_listing_error():
    with pytest.raises(ListingError):
        parse_entry({"uid": "u", "name": "x", "type": "file",
                     "activeRevision": {"claimedDigests": [1]}})


def test_r019_non_string_sha1_is_listing_error():
    with pytest.raises(ListingError):
        parse_entry({"uid": "u", "name": "x", "type": "file",
                     "activeRevision": {"claimedDigests": {"sha1": 123}}})


def test_r019_non_string_modification_time_is_listing_error():
    with pytest.raises(ListingError):
        parse_entry({"uid": "u", "name": "x", "type": "file", "modificationTime": 99})


def test_r019_bad_entry_inside_listing_surfaces_as_access_error():
    # access.py translates ListingError -> DriveAccessError; a single bad entry must ride that
    # same path, not escape as an AttributeError that aborts the scan.
    with pytest.raises(DriveAccessError):
        DriveAccess(runner=lambda p: '[{"uid":"u","name":123,"type":"file"}]').list_dir("/x")
