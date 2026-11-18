"""Safe append for the JSONL stores (R-013).

Readers skip invalid lines, so an aborted write that left a record without its
newline would otherwise be glued to the NEXT valid record, losing both. Under an
exclusive lock we therefore check the file's last byte and terminate an
unfinished record before appending. The new record goes out in one write and is
fsynced.
"""

from __future__ import annotations

import os

try:  # POSIX advisory lock; absent on some platforms
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]


def append_line(path: str, line: str) -> None:
    """Append ``line`` (without newline) as one complete, newline-terminated record."""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX)
        size = os.fstat(fd).st_size
        prefix = b""
        if size > 0 and os.pread(fd, 1, size - 1) != b"\n":
            prefix = b"\n"  # seal the torn record so it stays an isolated bad line
        data = prefix + line.encode("utf-8") + b"\n"
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view):]
        os.fsync(fd)
    finally:
        os.close(fd)  # also releases the lock
