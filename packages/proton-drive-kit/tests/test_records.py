import pytest

from proton_drive_kit.records import DriveRecord, parse_entry, parse_listing, ListingError
from fixtures import FILE_ENTRY, FOLDER_ENTRY, FILE_PLAIN_NAME, FILE_NO_REVISION


def test_parses_file_entry_with_claimed_sha1():
    r = parse_entry(FILE_ENTRY, parent_path="/my-files")
    assert isinstance(r, DriveRecord)
    assert r.is_folder is False
    assert r.sha1 == "a7f4db705d7cf4b7b378395a3e4c2f2e71cdc9fd"
    assert r.sha1_verified is False
    assert r.size == 125
    assert r.media_type == "text/plain"
    assert r.path == "/my-files/notes.txt"


def test_parses_folder_entry():
    r = parse_entry(FOLDER_ENTRY, parent_path="/my-files")
    assert r.is_folder is True
    assert r.sha1 is None


def test_name_accepts_plain_string():
    r = parse_entry(FILE_PLAIN_NAME)
    assert r.name == "Plain.txt"
    assert r.path == "Plain.txt"


def test_file_without_active_revision():
    r = parse_entry(FILE_NO_REVISION)
    assert r.sha1 is None
    assert r.is_folder is False


def test_parse_listing_rejects_non_list():
    with pytest.raises(ListingError):
        parse_listing('{"error":"x"}')


def test_parse_listing_parses_json_string_and_list():
    from fixtures import LISTING
    import json
    recs = parse_listing(json.dumps(LISTING), parent_path="/my-files")
    assert [r.name for r in recs] == ["FOLDER-A", "notes.txt"]
