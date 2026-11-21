"""Attachment-presence check: do a message's attachments already exist in the Drive?

Pure and decoupled. An attachment's sha1 is computed over the DECODED bytes so it
matches Proton Drive's content sha1 (``claimedDigests.sha1``). The Drive side is
reached only through the ``PresenceLookup`` Protocol — satisfied structurally by
``proton_drive_kit.datamap.DataMap.contains_sha1`` — so this module imports nothing
from the kit and does no fetching or writing itself.

The live attachment fetch that would feed ``extract_attachments`` is disk-sensitive
(downloading attachment bytes); it is wired in only in the supervised Phase 2, gated
by ``config.drive.presence_check.enabled`` (OFF by default).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from email import message_from_bytes
from typing import Protocol


def _sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


@dataclass(frozen=True)
class Attachment:
    filename: str  # best-effort display name ("" if the part gives none)
    sha1: str      # content sha1 (hex) — matches Proton Drive's claimed sha1
    size: int      # decoded byte length


def extract_attachments(raw: bytes) -> list[Attachment]:
    """Parse a raw RFC822 message and return its real attachments.

    A part counts as an attachment when it is a leaf (non-multipart) that is either
    marked ``Content-Disposition: attachment`` or carries a filename. Inline
    text/plain and text/html bodies without a filename are not attachments, and
    multipart containers are skipped.
    """
    msg = message_from_bytes(raw)
    out: list[Attachment] = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        disposition = (part.get_content_disposition() or "").lower()
        filename = part.get_filename()
        if disposition != "attachment" and filename is None:
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        out.append(Attachment(filename=filename or "", sha1=_sha1(payload), size=len(payload)))
    return out


class PresenceLookup(Protocol):
    """Anything that can answer 'which Drive paths hold this content sha1?'.

    ``proton_drive_kit.datamap.DataMap.contains_sha1`` satisfies this structurally.
    """

    def contains_sha1(self, sha1: str, under: str | None = None) -> list[str]:
        ...


@dataclass(frozen=True)
class AttachmentPresence:
    attachment: Attachment
    present_paths: tuple[str, ...]  # Drive paths holding the same content (scoped if `under`)

    @property
    def present(self) -> bool:
        return bool(self.present_paths)


def check_presence(
    attachments: list[Attachment],
    lookup: PresenceLookup,
    under: str | None = None,
) -> list[AttachmentPresence]:
    """For each attachment, the Drive paths (optionally restricted to a subtree)
    that already hold the same content by sha1. Read-only: no fetching, no writing.
    An attachment with no sha1 is reported as not present."""
    results: list[AttachmentPresence] = []
    for att in attachments:
        paths = tuple(lookup.contains_sha1(att.sha1, under=under)) if att.sha1 else ()
        results.append(AttachmentPresence(attachment=att, present_paths=paths))
    return results
