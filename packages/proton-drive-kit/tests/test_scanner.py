from proton_drive_kit.records import DriveRecord
from proton_drive_kit.access import DriveAccessError
from proton_drive_kit.scanner import scan, ScanIncomplete


def frec(path, sha1=None, uid="u"):
    return DriveRecord(uid=uid, parent_uid=None, name=path.rsplit("/", 1)[-1], path=path,
                       is_folder=False, sha1=sha1, sha1_verified=False, size=1, mtime=None,
                       media_type=None, shared_by_url=False)


def drec(path, uid="u"):
    return DriveRecord(uid=uid, parent_uid=None, name=path.rsplit("/", 1)[-1], path=path,
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


def test_list_error_emits_incomplete_marker_with_folder_uid():
    # A subfolder whose listing fails must NOT vanish silently: its subtree is
    # surfaced as a ScanIncomplete carrying the folder's own uid (so a differ can
    # match it to a baseline node), while siblings keep scanning.
    acc = FakeAccess({"/r": [drec("/r/bad", uid="UBAD"), frec("/r/ok", "S", uid="UOK")]},
                     errors={"/r/bad"})
    items = list(scan(acc, "/r"))
    assert {getattr(i, "path", None) for i in items} >= {"/r/bad", "/r/ok"}
    marks = [i for i in items if isinstance(i, ScanIncomplete)]
    assert len(marks) == 1
    assert (marks[0].path, marks[0].uid, marks[0].reason) == ("/r/bad", "UBAD", "list_error")


def test_max_depth_emits_incomplete_marker_with_uid():
    # A folder left unexpanded at the depth bound is an un-scanned subtree, not a
    # silently-complete leaf: it must emit a max_depth marker.
    acc = FakeAccess({"/r": [drec("/r/sub", uid="US")], "/r/sub": [frec("/r/sub/deep", "S")]})
    items = list(scan(acc, "/r", max_depth=1))
    marks = [i for i in items if isinstance(i, ScanIncomplete)]
    assert [(m.path, m.uid, m.reason) for m in marks] == [("/r/sub", "US", "max_depth")]
    assert "/r/sub/deep" not in {getattr(i, "path", None) for i in items}


def test_visited_set_terminates_on_cycle_and_lists_each_path_once():
    # A tree that refers back to an ancestor must not loop forever, and no path is
    # listed twice.
    acc = FakeAccess({"/r": [drec("/r/a", uid="UA")], "/r/a": [drec("/r", uid="UR")]})
    calls = []
    inner = acc.list_dir
    acc.list_dir = lambda p: (calls.append(p), inner(p))[1]
    items = list(scan(acc, "/r"))  # must terminate
    assert calls.count("/r") == 1 and calls.count("/r/a") == 1
    assert [i for i in items if isinstance(i, ScanIncomplete)] == []  # every listing succeeded


def test_successfully_listed_empty_folder_is_not_incomplete():
    # An empty folder that lists cleanly is complete, not a subtree gap.
    acc = FakeAccess({"/r": [drec("/r/empty", uid="UE")], "/r/empty": []})
    items = list(scan(acc, "/r"))
    assert [i for i in items if isinstance(i, ScanIncomplete)] == []
