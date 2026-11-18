"""Shared data models. MessageMeta is in-memory only; JournalEntry is persisted (hashes/metadata only)."""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Literal, Optional, Union

from pydantic import BaseModel, ConfigDict


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class MessageRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    account_hash: str
    mailbox: str
    uidvalidity: int
    uid: int
    message_id_hash: Optional[str] = None
    raw_hash: str

    @classmethod
    def from_source(
        cls,
        account_hash: str,
        mailbox: str,
        uidvalidity: int,
        uid: int,
        message_id: Optional[str],
        raw: bytes,
    ) -> "MessageRef":
        return cls(
            account_hash=account_hash,
            mailbox=mailbox,
            uidvalidity=uidvalidity,
            uid=uid,
            message_id_hash=_sha256(message_id.encode()) if message_id is not None else None,
            raw_hash=_sha256(raw),
        )

    def key(self) -> str:
        return f"{self.account_hash}:{self.mailbox}:{self.uidvalidity}:{self.uid}"


    def processing_key(self) -> str:
        """Stable processing identity for the idempotency marker (R-003):
        account + folder + UIDVALIDITY + UID + Message-ID hash. Hashes only."""
        return f"{self.key()}:{self.message_id_hash or '-'}"


class MessageMeta(BaseModel):
    """In-memory only for the classifier; never persisted."""

    ref: MessageRef
    subject: str
    from_addr: str
    to_addrs: list[str]
    date: Optional[datetime] = None
    in_reply_to: Optional[str] = None
    references: list[str] = []
    # Plaintext Message-ID: transient, used only for reply threading; never persisted.
    message_id: Optional[str] = None


class Classification(BaseModel):
    category: str
    labels: list[str]
    urgency: Literal["low", "normal", "high"]
    needs_reply: bool
    draft_subject: Optional[str] = None
    draft_body: Optional[str] = None


class ApplyLabel(BaseModel):
    model_config = ConfigDict(frozen=True)

    ref: MessageRef
    label: str


class StageDraft(BaseModel):
    model_config = ConfigDict(frozen=True)

    ref: MessageRef
    subject: str
    body: str
    to: Optional[str] = None
    in_reply_to: Optional[str] = None
    references: tuple[str, ...] = ()
    target: str
    label: Optional[str] = None


class DeleteToTrash(BaseModel):
    """Reversible removal (move to Trash). The only removal action."""

    model_config = ConfigDict(frozen=True)

    ref: MessageRef


Action = Union[ApplyLabel, StageDraft, DeleteToTrash]


class JournalEntry(BaseModel):
    """Persisted: metadata and hashes only, no message content."""

    ref_key: str
    account_hash: str
    mailbox: str
    uid: int
    message_id_hash: Optional[str] = None
    raw_hash: str
    action: Literal["classified", "drafted", "trashed", "marked_sent"]
    status: str
    ts: datetime
