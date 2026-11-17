"""In-memory MailStore for tests."""
from __future__ import annotations

from nucleon.models import MessageMeta, MessageRef


class FakeMailStore:
    def __init__(self) -> None:
        self._inbox: list[MessageMeta] = []
        self._bodies: dict[str, str] = {}
        self._sent: list[MessageMeta] = []
        self.appended_drafts: list[tuple[str, bytes]] = []
        self.labels: list[tuple[MessageRef, str]] = []
        self.moved_to_trash: list[MessageRef] = []
        self.seen: set[str] = set()

    def seed_inbox(self, meta: MessageMeta, body: str) -> None:
        self._inbox.append(meta)
        self._bodies[meta.ref.key()] = body

    def seed_sent(self, meta: MessageMeta) -> None:
        self._sent.append(meta)

    def list_window(self, mailbox: str, since_days: int, limit: int) -> list[MessageRef]:
        refs = [m.ref for m in self._inbox if m.ref.mailbox == mailbox]
        return refs[:limit]

    def fetch_meta(self, ref: MessageRef) -> MessageMeta:
        for m in self._inbox + self._sent:
            if m.ref.key() == ref.key():
                return m
        raise KeyError(ref.key())

    def fetch_body_text(self, ref: MessageRef) -> str:
        # Deliberately does not touch self.seen (models BODY.PEEK).
        return self._bodies[ref.key()]

    def list_sent(self, since_days: int, limit: int) -> list[MessageMeta]:
        return self._sent[:limit]

    def append_draft(self, target: str, mime_bytes: bytes) -> MessageRef:
        self.appended_drafts.append((target, mime_bytes))
        return MessageRef.from_source(
            "fake", target, 1, len(self.appended_drafts), None, mime_bytes
        )

    def set_label(self, ref: MessageRef, label: str) -> None:
        self.labels.append((ref, label))

    def move_to_trash(self, ref: MessageRef) -> None:
        self.moved_to_trash.append(ref)
