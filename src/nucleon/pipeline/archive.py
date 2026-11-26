"""The archive pipeline: compose the attachment loop over folders under one safety policy.

This is the policy layer around the pure ``mailfetch.loop`` mechanism. The loop decides, per
message, present/absent/unknown and (in delete mode) whether every attachment is accounted
for. This layer adds the cross-cutting rules the owner fixed for a LIVE connection:

  - **Deletion requires an Abdruck.** ``do_delete=True`` without a ``recorder`` is a
    misconfiguration and is refused up front (fail-closed) here. R-033: the same guarantee is
    ALSO enforced inside ``process_folder`` itself, so a direct loop call cannot delete without
    a recorder either — this layer's early raise is the friendly check, not the only one.
  - **Default-off.** ``do_delete`` defaults to False: the pipeline caches→compares→files and
    keeps every original. Deletion is an explicit, separately-enabled step.

Everything stays injected (fetcher/lookup/stager/deleter/recorder), so this composes without
touching IMAP, the Drive, or Chromium itself.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from nucleon.mailfetch.budget import DiskBudget
from nucleon.mailfetch.loop import (
    Deleter,
    Fetcher,
    FolderReport,
    Recorder,
    Stager,
    process_folder,
)
from nucleon.presence import PresenceLookup


@dataclass
class ArchiveReport:
    folders: list[FolderReport] = field(default_factory=list)

    def _sum(self, attr: str) -> int:
        return sum(getattr(f, attr) for f in self.folders)

    @property
    def processed(self) -> int: return self._sum("processed")
    @property
    def staged(self) -> int: return self._sum("staged")
    @property
    def already_present(self) -> int: return self._sum("already_present")
    @property
    def deleted(self) -> int: return self._sum("deleted")
    @property
    def kept_unknown(self) -> int: return self._sum("kept_unknown")
    @property
    def kept_no_abdruck(self) -> int: return self._sum("kept_no_abdruck")
    @property
    def errors(self) -> int: return self._sum("errors")


def run_archive(
    folders: Iterable[str],
    fetcher: Fetcher,
    lookup: PresenceLookup,
    stager: Stager,
    budget: DiskBudget,
    *,
    deleter: Optional[Deleter] = None,
    recorder: Optional[Recorder] = None,
    under: Optional[str] = None,
    do_delete: bool = False,
) -> ArchiveReport:
    """Run the cache→compare→file→(Abdruck→delete) loop over each folder.

    Raises ``ValueError`` before doing anything if deletion is requested without the means to
    do it reversibly (a deleter AND a recorder). With ``do_delete=False`` (the default) no
    original is ever removed, regardless of the other arguments."""
    if do_delete:
        if deleter is None:
            raise ValueError("do_delete=True requires a deleter")
        if recorder is None:
            # Fail-closed: no delete without a durable Abdruck of the source first.
            raise ValueError(
                "do_delete=True requires a recorder (Abdruck); refusing to delete "
                "source mail without making a durable record first"
            )
    report = ArchiveReport()
    for folder in folders:
        report.folders.append(
            process_folder(
                folder, fetcher, lookup, stager, deleter, budget,
                under=under, do_delete=do_delete, recorder=recorder,
            )
        )
    return report
