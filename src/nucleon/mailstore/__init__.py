"""MailStore interface. Read-only except draft/label/trash; there is no send method."""
from __future__ import annotations

from typing import Iterator, Protocol

from nucleon.models import MessageMeta, MessageRef


class StaleReferenceError(Exception):
    """The folder's UIDVALIDITY no longer matches the reference (R-002).

    The referenced UID may now denote a different message; treat the message
    as vanished and take no action on it.
    """


class MailStore(Protocol):
    def system_folders(self) -> frozenset[str]:
        """Provider-flagged system/special folders; never valid label targets."""
        ...

    def list_window(self, mailbox: str, since_days: int, limit: int) -> list[MessageRef]: ...

    def iter_window(self, mailbox: str, since_days: int) -> Iterator[MessageRef]:
        """All refs in the window, oldest first, unlimited (R-005): the caller
        applies its processing limit after its own marker/journal filter."""
        ...

    def fetch_meta(self, ref: MessageRef) -> MessageMeta: ...

    def fetch_body_text(self, ref: MessageRef) -> str:
        """Must not change message flags (BODY.PEEK semantics)."""
        ...

    def find_draft(self, target: str, message_id: str) -> list[MessageRef]:
        """Read-only lookup of drafts in ``target`` by Message-ID (R-004)."""
        ...

    def list_sent(self, since_days: int, limit: int) -> list[MessageMeta]: ...

    def append_draft(self, target: str, mime_bytes: bytes) -> MessageRef: ...

    def set_label(self, ref: MessageRef, label: str) -> None: ...

    def move_to_trash(self, ref: MessageRef) -> None:
        """Reversible move to Trash; never expunge/purge."""
        ...
