from proton_drive_kit.records import DriveRecord
from proton_drive_kit.access import DriveAccessError
from proton_drive_kit.scanner import scan


def frec(path, sha1=None):
    return DriveRecord(uid="u", parent_uid=None, name=path.rsplit("/", 1)[-1], path=path,
                       is_folder=False, sha1=sha1, sha1_verified=False, size=1, mtime=None,
                       media_type=None, shared_by_url=False)


def drec(path):
    return DriveRecord(uid="u", parent_uid=None, name=path.rsplit("/", 1)[-1], path=path,
                       is_folder=True, sha1=None, sha1_verified=False, size=None, mtime=None,
                       media_type=None, shared_by_url=False)


class FakeAccess:
    def __init__(self, tree, errors=()):
        self.tree = tree
        self.errors = set(errors)

    def list_dir(self, abspath):
        if abspath in self.errors:
            raise DriveAccessError("boom " + abspath)
        return list(self.tree.get(abspath, []))


def test_scans_one_level():
    acc = FakeAccess({"/r": [frec("/r/a.txt", "S"), drec("/r/sub")]})
    paths = {r.path for r in scan(acc, "/r")}
    assert paths == {"/r/a.txt", "/r/sub"}


def test_recurses_into_folders():
    acc = FakeAccess({"/r": [drec("/r/sub")], "/r/sub": [frec("/r/sub/f.txt", "S")]})
    paths = {r.path for r in scan(acc, "/r")}
    assert "/r/sub/f.txt" in paths


def test_max_depth_stops_recursion():
    acc = FakeAccess({"/r": [drec("/r/sub")], "/r/sub": [frec("/r/sub/deep", "S")]})
    paths = {r.path for r in scan(acc, "/r", max_depth=1)}
    assert "/r/sub" in paths and "/r/sub/deep" not in paths


def test_subfolder_access_error_is_skipped():
    acc = FakeAccess({"/r": [drec("/r/bad"), frec("/r/ok", "S")]}, errors={"/r/bad"})
    paths = {r.path for r in scan(acc, "/r")}
    assert "/r/ok" in paths and "/r/bad" in paths
