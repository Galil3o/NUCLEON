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
import re
from dataclasses import dataclass
from email import message_from_bytes
from typing import Protocol

_SHA1_RE = re.compile(r"\A[0-9a-f]{40}\Z")


def _sha1(data: bytes) -> str:
    # Content addressing (matches Proton Drive's claimed content sha1), not a security
    # primitive: usedforsecurity=False documents that and permits FIPS environments.
    return hashlib.sha1(data, usedforsecurity=False).hexdigest()


def _is_valid_sha1(value: str) -> bool:
    """True only for a well-formed lowercase 40-hex SHA-1 (what ``_sha1`` produces).
    A non-empty but malformed value is not a comparable digest, so the caller treats it
    as inconclusive rather than a basis for 'absent' (R-021 evidence boundary)."""
    return bool(value) and _SHA1_RE.match(value) is not None


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
    An optional ``has_digestless(under)`` lets the lookup report that the scope
    contains files with no comparable digest, and an optional
    ``coverage_incomplete(under)`` lets it report that part of the scope was never
    scanned — either one makes a hash miss inconclusive.
    """

    def contains_sha1(self, sha1: str, under: str | None = None) -> list[str]:
        ...


@dataclass(frozen=True)
class AttachmentPresence:
    attachment: Attachment
    present_paths: tuple[str, ...]  # Drive paths holding the same content (scoped if `under`)
    status: str = "absent"          # "present" | "absent" | "unknown"

    @property
    def present(self) -> bool:
        return self.status == "present"


def check_presence(
    attachments: list[Attachment],
    lookup: PresenceLookup,
    under: str | None = None,
) -> list[AttachmentPresence]:
    """For each attachment, a three-valued presence against the Drive (read-only):
      - ``present``: at least one Drive path (optionally under a subtree) holds the
        same content by sha1;
      - ``unknown``: the miss is inconclusive, so the caller must not treat it as
        "safe to store a duplicate". This covers three cases: the attachment has no
        digest of its own; the lookup cannot prove digest-comparable coverage (it has
        no ``has_digestless``); or the scope contains files WITHOUT a comparable digest
        (``lookup.has_digestless(under)`` is True);
      - ``absent``: ONLY when the attachment has a valid sha1 AND the lookup positively
        proves the scope is fully digest-comparable (``has_digestless`` exists and
        returns False) AND the lookup does not report the scope as partly un-scanned
        (``coverage_incomplete(under)`` is not True), and still holds no match.
    An attachment with no sha1 of its own can never match and is never ``present``;
    because its absence cannot be proven either, it is ``unknown``, never ``absent``.
    A scope that was only partly scanned (an un-walked subtree) can never yield a
    definite ``absent``: the missing content could hide in the un-scanned region."""
    has_digestless = getattr(lookup, "has_digestless", None)
    can_prove_coverage = callable(has_digestless)
    coverage_incomplete = getattr(lookup, "coverage_incomplete", None)
    results: list[AttachmentPresence] = []
    for att in attachments:
        if not _is_valid_sha1(att.sha1):
            # No valid digest on our side (missing or malformed): the content cannot be
            # compared, so absence can never be proven -> inconclusive (R-021 edge).
            results.append(AttachmentPresence(attachment=att, present_paths=(), status="unknown"))
            continue
        paths = tuple(lookup.contains_sha1(att.sha1, under=under))
        if paths:
            status = "present"
        elif (can_prove_coverage and has_digestless(under) is False
              and not (callable(coverage_incomplete) and coverage_incomplete(under) is True)):
            # The lookup positively reports (explicit False, not None/truthy) that the
            # scope is fully digest-comparable, AND does not report it as partly
            # un-scanned, and holds no match -> definitely absent.
            status = "absent"
        else:
            # Either the lookup cannot prove digest-comparable coverage (no
            # ``has_digestless``), or the scope contains digestless files -> the miss
            # is inconclusive, never a definite "absent" (R-021 edges).
            status = "unknown"
        results.append(AttachmentPresence(attachment=att, present_paths=paths, status=status))
    return results
