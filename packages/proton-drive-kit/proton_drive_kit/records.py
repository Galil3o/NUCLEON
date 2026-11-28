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
        v = n.get("value", "")
        if v is not None and not isinstance(v, str):
            raise ListingError("entry name.value is not a string")
        return v or ""
    if n is not None and not isinstance(n, str):
        raise ListingError("entry name is not a string")
    return n or ""


def parse_entry(entry: dict, parent_path: str = "") -> DriveRecord:
    # R-019: validate nested field types at the parser boundary. A malformed entry
    # (numeric name, activeRevision/claimedDigests that is not an object, non-string
    # sha1 or time) must raise ListingError — which access.py maps to DriveAccessError —
    # rather than an AttributeError that aborts the whole scan and, via the empty result,
    # reads to the caller as "folder empty".
    name = _name_of(entry)
    seg = name.replace("/", "∕")  # a name's '/' is not a path separator; neutralize it
    ar = entry.get("activeRevision")
    if ar is None:
        ar = {}
    elif not isinstance(ar, dict):
        raise ListingError("entry activeRevision is not an object")
    digests = ar.get("claimedDigests")
    if digests is None:
        digests = {}
    elif not isinstance(digests, dict):
        raise ListingError("entry claimedDigests is not an object")
    sha1 = digests.get("sha1")
    if sha1 is not None and not isinstance(sha1, str):
        raise ListingError("entry claimedDigests.sha1 is not a string")
    size = entry.get("totalStorageSize")
    if size is None:
        size = ar.get("storageSize")
    mt_raw = entry.get("modificationTime")
    if mt_raw is not None and not isinstance(mt_raw, str):
        raise ListingError("entry modificationTime is not a string")
    mt = (mt_raw or "")[:19] or None
    return DriveRecord(
        uid=entry.get("uid", ""),
        parent_uid=entry.get("parentUid"),
        name=name,
        # Canonicalize the join: a parent_path of "/" (scanning the root) must yield "/A",
        # never "//A", or every prefix/shield comparison on that path silently misfires.
        path=(f"{parent_path.rstrip('/')}/{seg}" if parent_path else seg),
        is_folder=(entry.get("type") == "folder"),
        sha1=(sha1 or None),
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
    out: list[DriveRecord] = []
    for e in data:
        if not isinstance(e, dict):
            raise ListingError("listing element is not an object")
        out.append(parse_entry(e, parent_path))
    return out
