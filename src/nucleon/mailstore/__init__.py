"""MailStore interface. Read-only except draft/label/trash; there is no send method."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Protocol

from nucleon.models import MessageMeta, MessageRef


class StaleReferenceError(Exception):
    """The folder's UIDVALIDITY no longer matches the reference (R-002).

    The referenced UID may now denote a different message; treat the message
    as vanished and take no action on it.
    """


class PreflightError(Exception):
    """The mailbox cannot safely be written to (R-010); nothing was written."""


@dataclass(frozen=True)
class PreflightRequest:
    """What the first write needs. Metadata only (folder names, switches)."""

    draft_targets: tuple[str, ...] = ()  # "drafts" / "outbox" (provider-mapped by the store)
    folders: tuple[str, ...] = ()  # label/target folders that must exist
    need_move: bool = False  # Trash moves planned: needs the MOVE capability
    create_missing: bool = False  # operator opt-in: create missing, non-system folders


@dataclass(frozen=True)
class PreflightReport:
    problems: tuple[str, ...] = ()  # block the write run
    warnings: tuple[str, ...] = ()  # degraded but safe (e.g. no UIDPLUS)
    created: tuple[str, ...] = ()  # folders created (only with create_missing)

    @property
    def ok(self) -> bool:
        return not self.problems


@dataclass(frozen=True)
class BodyFetch:
    """Result of a budgeted body fetch (R-011)."""

    text: str
    truncated: bool = False
    # Size in bytes of the text part as the server reported it (None if unknown).
    original_bytes: int | None = None


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

    def fetch_body(self, ref: MessageRef) -> BodyFetch:
        """Budgeted body fetch (R-011): checks size/structure BEFORE loading,
        loads only the needed text part with a byte limit, raises
        ``BudgetExceeded`` for an over-size message, flags truncation."""
        ...

    def preflight(self, req: PreflightRequest) -> PreflightReport:
        """Check folders, namespace and required capabilities BEFORE the first
        write (R-010). Read-only unless ``req.create_missing`` is set; fails
        closed (raises) if the server cannot answer."""
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
