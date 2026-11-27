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


def test_no_wrapper_no_runner_no_env_fails_closed(monkeypatch):
    # The public kit must NOT silently shell out to a hardcoded operator path. With no
    # runner, no wrapper argument and no env var, construction fails clearly.
    monkeypatch.delenv("PROTON_DRIVE_LIST_WRAPPER", raising=False)
    with pytest.raises(DriveAccessError):
        DriveAccess()


def test_env_var_configures_wrapper(monkeypatch):
    monkeypatch.setenv("PROTON_DRIVE_LIST_WRAPPER", "/opt/dep/drive_list.sh")
    DriveAccess()  # resolves from the env var; must not raise at construction


def test_explicit_wrapper_arg_configures_wrapper(monkeypatch):
    monkeypatch.delenv("PROTON_DRIVE_LIST_WRAPPER", raising=False)
    DriveAccess(wrapper="/opt/dep/drive_list.sh")  # explicit path; must not raise


def test_injected_runner_needs_no_wrapper(monkeypatch):
    monkeypatch.delenv("PROTON_DRIVE_LIST_WRAPPER", raising=False)
    assert DriveAccess(runner=lambda p: "[]").list_dir("/x") == []


def test_no_hardcoded_home_path_in_module():
    import proton_drive_kit.access as access_mod
    # the old hardcoded operator path must be gone from the public package
    assert getattr(access_mod, "DEFAULT_WRAPPER", "") == "" or "/home/" not in access_mod.DEFAULT_WRAPPER
