"""BFS scanner over a DriveAccess-like object. Yields every record under a root.

A subfolder whose listing raises DriveAccessError is skipped (its subtree is not
expanded); scanning continues. ``max_depth`` bounds how many listing levels deep
the scan goes (the root is level 1).
"""
from __future__ import annotations

from typing import Iterator

from proton_drive_kit.access import DriveAccessError
from proton_drive_kit.records import DriveRecord


def scan(access, root: str, *, max_depth: int = 12) -> Iterator[DriveRecord]:
    queue: list[tuple[str, int]] = [(root, 1)]
    while queue:
        path, level = queue.pop(0)
        if level > max_depth:
            continue
        try:
            recs = access.list_dir(path)
        except DriveAccessError:
            continue
        for r in recs:
            yield r
            if r.is_folder:
                queue.append((r.path, level + 1))
