"""MailStore interface. Read-only except draft/label/trash; there is no send method."""
from __future__ import annotations

from typing import Protocol

from nucleon.models import MessageMeta, MessageRef


class MailStore(Protocol):
    def list_window(self, mailbox: str, since_days: int, limit: int) -> list[MessageRef]: ...

    def fetch_meta(self, ref: MessageRef) -> MessageMeta: ...

    def fetch_body_text(self, ref: MessageRef) -> str:
        """Must not change message flags (BODY.PEEK semantics)."""
        ...

    def list_sent(self, since_days: int, limit: int) -> list[MessageMeta]: ...

    def append_draft(self, target: str, mime_bytes: bytes) -> MessageRef: ...

    def set_label(self, ref: MessageRef, label: str) -> None: ...

    def move_to_trash(self, ref: MessageRef) -> None:
        """Reversible move to Trash; never expunge/purge."""
        ...
