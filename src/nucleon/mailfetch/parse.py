"""Attachment extraction WITH decoded payloads, for staging.

``nucleon.presence.extract_attachments`` returns metadata only (filename, sha1, size).
The fetch loop additionally needs the decoded bytes to stage an absent attachment, so
this mirror keeps the payload. sha1/size are computed identically (over decoded bytes),
so the two stay consistent for the same message.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from email import message_from_bytes


@dataclass(frozen=True)
class AttachmentBlob:
    filename: str
    sha1: str
    size: int
    data: bytes


def extract_with_payload(raw: bytes) -> list[AttachmentBlob]:
    msg = message_from_bytes(raw)
    out: list[AttachmentBlob] = []
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
        out.append(AttachmentBlob(
            filename=filename or "",
            sha1=hashlib.sha1(payload, usedforsecurity=False).hexdigest(),
            size=len(payload),
            data=payload,
        ))
    return out
