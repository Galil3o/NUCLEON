"""Parse one Proton Drive CLI JSON entry into a typed DriveRecord.

The CLI's `sha1` is a CLIENT-CLAIMED digest (`claimedDigests.sha1`, usually
`sha1Verified: false`); we store it and the flag, never presenting it as verified.
"""
from __future__ import annotations

import json
from dataclasses import dataclass


class ListingError(Exception):
    """A CLI listing payload was not a JSON array of entries."""


@dataclass(frozen=True)
class DriveRecord:
    uid: str
    parent_uid: str | None
    name: str
    path: str
    is_folder: bool
    sha1: str | None
    sha1_verified: bool
    size: int | None
    mtime: str | None
    media_type: str | None
    shared_by_url: bool


def _name_of(entry: dict) -> str:
    n = entry.get("name")
    if isinstance(n, dict):
        return n.get("value", "") or ""
    return n or ""


def parse_entry(entry: dict, parent_path: str = "") -> DriveRecord:
    name = _name_of(entry)
    ar = entry.get("activeRevision") or {}
    digests = ar.get("claimedDigests") or {}
    size = entry.get("totalStorageSize")
    if size is None:
        size = ar.get("storageSize")
    mt = (entry.get("modificationTime") or "")[:19] or None
    return DriveRecord(
        uid=entry.get("uid", ""),
        parent_uid=entry.get("parentUid"),
        name=name,
        path=(f"{parent_path}/{name}" if parent_path else name),
        is_folder=(entry.get("type") == "folder"),
        sha1=(digests.get("sha1") or None),
        sha1_verified=bool(digests.get("sha1Verified", False)),
        size=size,
        mtime=mt,
        media_type=(entry.get("mediaType") or None),
        shared_by_url=bool(entry.get("isSharedByUrl", False)),
    )


def parse_listing(data, parent_path: str = "") -> list[DriveRecord]:
    if isinstance(data, (str, bytes)):
        try:
            data = json.loads(data)
        except (ValueError, TypeError) as e:
            raise ListingError(f"listing is not valid JSON: {e}") from e
    if not isinstance(data, list):
        raise ListingError("listing is not a JSON array of entries")
    return [parse_entry(e, parent_path) for e in data]
