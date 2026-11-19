import json

import pytest

from proton_drive_kit.access import DriveAccess, DriveAccessError
from fixtures import LISTING


def test_list_dir_parses_fixture():
    def runner(abspath):
        assert abspath == "/my-files"
        return json.dumps(LISTING)

    recs = DriveAccess(runner=runner).list_dir("/my-files")
    assert [r.name for r in recs] == ["FOLDER-A", "notes.txt"]
    assert all(r.path.startswith("/my-files/") for r in recs)


def test_runner_error_becomes_access_error():
    def runner(abspath):
        raise RuntimeError("process failed")

    with pytest.raises(DriveAccessError):
        DriveAccess(runner=runner).list_dir("/x")


def test_error_object_becomes_access_error():
    def runner(abspath):
        return '{"error":"nope"}'

    with pytest.raises(DriveAccessError):
        DriveAccess(runner=runner).list_dir("/x")
