"""Stager: put one attachment's bytes into its routed Drive folder.

``RoutingStager`` implements the ``mailfetch.loop.Stager`` protocol. It is pure w.r.t.
I/O: the actual upload is an injected ``Uploader`` callable, so tests use a mock. The
live adapter ``ProtonDriveUploader`` shells to the Proton Drive CLI with ``-f rename``
(same content is auto-skipped by the CLI -> idempotent; a name clash with different
content gets a unique suffix).
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Callable, Mapping, Optional

from nucleon.filing.router import Router

# (data, filename, parent_folder) -> staged remote path
Uploader = Callable[[bytes, str, str], str]


@dataclass
class RoutingStager:
    router: Router
    uploader: Uploader

    def stage(self, filename: str, data: bytes, sha1: str) -> str:
        meta = {"filename": filename, "sha1": sha1, "size": len(data)}
        target = self.router.target_for(meta)
        return self.uploader(data, filename, target)


class ProtonDriveUploader:
    """Live uploader (not used in unit tests against the real CLI). Writes the bytes to a
    temp file and runs ``fs upload -f rename <tmp> <parent>``. ``runner`` is injected so the
    command construction can be unit-tested without touching the CLI."""

    def __init__(self, bin_path: str = "/opt/proton-drive-cli/0.9.0/proton-drive",
                 runner: Callable = subprocess.run, tmpdir: Optional[str] = None):
        self.bin = bin_path
        self.runner = runner
        self.tmpdir = tmpdir

    def __call__(self, data: bytes, filename: str, parent: str) -> str:
        # R-035b: the mail-supplied filename reaches a filesystem path and a CLI argument.
        # Reduce it to a single validated basename so it cannot escape the temp dir or inject
        # path segments. R-035c: the returned path is BEST-EFFORT — the provider may rename on
        # a name clash (``-f rename``), so the archive decision must rest on content identity
        # (the attachment sha1 checked by the presence lookup), never on this returned name.
        safe = os.path.basename(filename or "")
        if not safe or safe in (".", "..") or safe.startswith("."):
            safe = "attachment.bin"
        d = tempfile.mkdtemp(dir=self.tmpdir)
        local = os.path.join(d, safe)
        try:
            with open(local, "wb") as f:
                f.write(data)
            res = self.runner([self.bin, "fs", "upload", "-f", "rename", local, parent],
                              capture_output=True, text=True, timeout=300)
            rc = getattr(res, "returncode", 0)
            if rc != 0:
                raise OSError("upload failed rc=%s: %s" % (rc, getattr(res, "stderr", "")))
        finally:
            try:
                os.remove(local); os.rmdir(d)
            except OSError:
                pass
        return parent.rstrip("/") + "/" + safe
