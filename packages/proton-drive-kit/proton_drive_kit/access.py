"""Access layer: list a Proton Drive directory via the CLI, behind an injectable runner.

The default runner shells out to a deployment-specific wrapper script (which handles the
dbus/keyring unlock). That wrapper's path is NOT hardcoded in this package: configure it
per deployment via the ``PROTON_DRIVE_LIST_WRAPPER`` environment variable or the
``wrapper=`` argument, or bypass it entirely by injecting a ``runner=``. Tests inject a
fake runner and never touch a live Drive.
"""
from __future__ import annotations

import os
import subprocess
from typing import Callable

from proton_drive_kit.records import DriveRecord, ListingError, parse_listing

Runner = Callable[[str], str]

# The wrapper-script path is deployment-specific and must not be baked into this public
# package. It is read from this environment variable when no explicit ``wrapper=`` is given.
ENV_WRAPPER = "PROTON_DRIVE_LIST_WRAPPER"


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


def _resolve_wrapper(wrapper: str | None) -> str:
    w = wrapper or os.environ.get(ENV_WRAPPER)
    if not w:
        raise DriveAccessError(
            "no Drive list wrapper configured: pass wrapper=..., inject a runner=..., or set "
            f"the {ENV_WRAPPER} environment variable to the wrapper script path"
        )
    return w


class DriveAccess:
    def __init__(self, runner: Runner | None = None, *, wrapper: str | None = None) -> None:
        # Fail closed: with no runner we need a configured wrapper path (arg or env), never
        # a hardcoded guess at where the wrapper lives on some particular machine.
        self._runner: Runner = (
            runner if runner is not None else _default_runner(_resolve_wrapper(wrapper))
        )

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
