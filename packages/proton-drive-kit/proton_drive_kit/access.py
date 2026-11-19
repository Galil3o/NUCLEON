"""Access layer: list a Proton Drive directory via the CLI, behind an injectable runner.

The default runner shells out to the dbus/keyring wrapper script (empty-passphrase
keyring); tests inject a fake runner and never touch a live Drive.
"""
from __future__ import annotations

import subprocess
from typing import Callable

from proton_drive_kit.records import DriveRecord, ListingError, parse_listing

Runner = Callable[[str], str]

DEFAULT_WRAPPER = "/home/claude/drive_list.sh"


class DriveAccessError(Exception):
    """A Drive listing could not be obtained or parsed."""


def _default_runner(wrapper: str) -> Runner:
    def run(abspath: str) -> str:
        p = subprocess.run(
            ["bash", wrapper, abspath], capture_output=True, text=True, timeout=120
        )
        if p.returncode != 0:
            raise DriveAccessError(
                f"drive list failed rc={p.returncode}: {(p.stderr or '').strip()[:120]}"
            )
        return p.stdout

    return run


class DriveAccess:
    def __init__(self, runner: Runner | None = None, *, wrapper: str = DEFAULT_WRAPPER) -> None:
        self._runner: Runner = runner or _default_runner(wrapper)

    def list_dir(self, abspath: str) -> list[DriveRecord]:
        try:
            out = self._runner(abspath)
        except DriveAccessError:
            raise
        except Exception as e:  # runner is operator-supplied; normalize any failure
            raise DriveAccessError(f"runner failed for {abspath}: {e!r}") from e
        try:
            return parse_listing(out, parent_path=abspath)
        except ListingError as e:
            raise DriveAccessError(f"bad listing for {abspath}: {e}") from e
