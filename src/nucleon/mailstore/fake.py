"""In-memory MailStore for tests."""
from __future__ import annotations

from email import message_from_bytes
from typing import Iterator

from nucleon.budgets import BudgetExceeded
from nucleon.mailstore import (
    BodyFetch,
    PreflightReport,
    PreflightRequest,
    StaleReferenceError,
)
from nucleon.models import MessageMeta, MessageRef


class FakeMailStore:
    def __init__(self, max_message_bytes: int | None = None,
                 max_body_bytes: int | None = None,
                 append_returns_uid: bool = True) -> None:
        self._append_returns_uid = append_returns_uid  # False: provider gives no APPENDUID
        self.preflight_requests: list[PreflightRequest] = []
        self.preflight_problems: tuple[str, ...] = ()
        self.write_log: list[str] = []  # "preflight" / "append" / "label" / "trash", in order
        self._max_message = max_message_bytes
        self._max_body = max_body_bytes
        self._sizes: dict[str, int] = {}
        self._inbox: list[MessageMeta] = []
        self._bodies: dict[str, str] = {}
        self._sent: list[MessageMeta] = []
        self.appended_drafts: list[tuple[str, bytes]] = []
        self.labels: list[tuple[MessageRef, str]] = []
        self.moved_to_trash: list[MessageRef] = []
        self.seen: set[str] = set()
        self.system_folder_names: frozenset[str] = frozenset()
        self._validity: dict[str, int] = {}

    def system_folders(self) -> frozenset[str]:
        return self.system_folder_names

    def set_uidvalidity(self, mailbox: str, value: int) -> None:
        """Simulate the server renumbering a folder (UIDs no longer comparable)."""
        self._validity[mailbox] = value

    def _check(self, ref: MessageRef) -> None:
        if self._validity.get(ref.mailbox, 1) != ref.uidvalidity:
            raise StaleReferenceError(ref.mailbox)

    def seed_inbox(self, meta: MessageMeta, body: str, size: int | None = None) -> None:
        self._inbox.append(meta)
        self._bodies[meta.ref.key()] = body
        self._sizes[meta.ref.key()] = size if size is not None else len(body.encode())

    def seed_sent(self, meta: MessageMeta) -> None:
        self._sent.append(meta)

    def list_window(self, mailbox: str, since_days: int, limit: int) -> list[MessageRef]:
        refs = [m.ref for m in self._inbox if m.ref.mailbox == mailbox]
        return refs[:limit]

    def iter_window(self, mailbox: str, since_days: int) -> Iterator[MessageRef]:
        yield from (m.ref for m in self._inbox if m.ref.mailbox == mailbox)

    def fetch_meta(self, ref: MessageRef) -> MessageMeta:
        self._check(ref)
        for m in self._inbox + self._sent:
            if m.ref.key() == ref.key():
                return m
        raise KeyError(ref.key())

    def fetch_body_text(self, ref: MessageRef) -> str:
        self._check(ref)
        # Deliberately does not touch self.seen (models BODY.PEEK).
        return self._bodies[ref.key()]

    def fetch_body(self, ref: MessageRef) -> BodyFetch:
        self._check(ref)
        size = self._sizes[ref.key()]
        if self._max_message is not None and size > self._max_message:
            raise BudgetExceeded("message_size", self._max_message, size)
        text = self._bodies[ref.key()]
        raw = text.encode()
        if self._max_body is not None and len(raw) > self._max_body:
            return BodyFetch(raw[: self._max_body].decode("utf-8", "ignore"), True, len(raw))
        return BodyFetch(text, False, len(raw))

    def find_draft(self, target: str, message_id: str) -> list[MessageRef]:
        out = []
        for i, (t, raw) in enumerate(self.appended_drafts, start=1):
            if t == target and message_from_bytes(raw)["Message-ID"] == message_id:
                out.append(MessageRef.from_source("fake", t, 1, i, message_id, raw))
        return out

    def list_sent(self, since_days: int, limit: int) -> list[MessageMeta]:
        return self._sent[:limit]

    def preflight(self, req: PreflightRequest) -> PreflightReport:
        self.preflight_requests.append(req)
        self.write_log.append("preflight")
        return PreflightReport(problems=self.preflight_problems)

    def append_draft(self, target: str, mime_bytes: bytes) -> MessageRef:
        self.write_log.append("append")
        self.appended_drafts.append((target, mime_bytes))
        mid = message_from_bytes(mime_bytes)["Message-ID"]
        if not self._append_returns_uid:  # like a server without UIDPLUS: location unknown
            return MessageRef.from_source("fake", target, 0, 0, mid, mime_bytes)
        return MessageRef.from_source(
            "fake", target, 1, len(self.appended_drafts), mid, mime_bytes
        )

    def set_label(self, ref: MessageRef, label: str) -> None:
        self._check(ref)
        self.write_log.append("label")
        self.labels.append((ref, label))

    def move_to_trash(self, ref: MessageRef) -> None:
        self._check(ref)
        self.write_log.append("trash")
        self.moved_to_trash.append(ref)
