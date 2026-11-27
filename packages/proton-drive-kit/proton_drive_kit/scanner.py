"""BFS scanner over a DriveAccess-like object.

Yields every record under a root. Where a subtree cannot be fully walked, the scan
does NOT drop it silently — it yields a :class:`ScanIncomplete` marker so a consumer
can record that region as un-scanned (an ``ERR`` row in a map; ``unknown`` rather than
``absent`` in a presence check). Two things produce a marker:

* ``list_error`` — a subfolder's ``list_dir`` raised ``DriveAccessError``; its subtree
  is not expanded, but the gap is reported.
* ``max_depth``  — a folder is left unexpanded because ``max_depth`` was reached
  (the root is level 1).

Each marker carries the folder's ``path`` and its Proton Drive ``uid`` (``None`` only
for the scan root), so a map/differ can match the gap to a baseline node by uid. A
visited set keyed on the listing path makes the walk terminate even if the tree refers
back to an ancestor, and guarantees no path is listed twice.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Union

from proton_drive_kit.access import DriveAccessError
from proton_drive_kit.records import DriveRecord


@dataclass(frozen=True)
class ScanIncomplete:
    """A folder whose subtree was not (fully) walked.

    ``reason`` is ``"list_error"`` (the listing raised) or ``"max_depth"`` (the depth
    bound stopped expansion). ``uid`` is the folder's Proton Drive uid when known
    (``None`` for the scan root).
    """

    path: str
    uid: str | None
    reason: str


ScanItem = Union[DriveRecord, ScanIncomplete]


def scan(access, root: str, *, max_depth: int = 12) -> Iterator[ScanItem]:
    """Breadth-first walk from ``root``. Yields a :class:`DriveRecord` for every listed
    entry and a :class:`ScanIncomplete` for every folder whose subtree could not be
    (fully) walked. The root path itself is only listed, never yielded as a record."""
    # Canonical root: "" and "/" both mean the tree root. Without this, scanning "" yields
    # relative entry paths that never match an absolute "/" query (false 'absent').
    root = root.rstrip("/") or "/"
    queue: list[tuple[str, int, str | None]] = [(root, 1, None)]
    visited: set[str] = set()
    while queue:
        path, level, uid = queue.pop(0)
        if path in visited:
            continue  # already listed via another reference: never list a path twice
        visited.add(path)
        if level > max_depth:
            yield ScanIncomplete(path=path, uid=uid, reason="max_depth")
            continue
        try:
            recs = access.list_dir(path)
        except DriveAccessError:
            yield ScanIncomplete(path=path, uid=uid, reason="list_error")
            continue
        for r in recs:
            yield r
            if r.is_folder:
                queue.append((r.path, level + 1, r.uid))
