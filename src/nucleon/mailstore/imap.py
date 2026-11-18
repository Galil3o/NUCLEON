"""IMAP MailStore adapter over an ``imaplib.IMAP4``-shaped connection.

Safety rules: reads use BODY.PEEK only (never sets \\Seen); there is no send
path; delete is a targeted, reversible UID MOVE to Trash (never EXPUNGE/purge).
Live Proton Bridge acceptance is deferred (post-v0.1).
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from email import message_from_bytes
from email.message import Message
from email.policy import default as _policy
from email.utils import getaddresses, parsedate_to_datetime
from typing import Any, Optional

from nucleon.models import MessageMeta, MessageRef

# LIST attributes (RFC 3501 / RFC 6154 SPECIAL-USE) that mark a folder as
# non-label: unselectable containers and system/special-use folders.
_SYSTEM_FLAGS = frozenset({
    "\\noselect", "\\nonexistent", "\\all", "\\trash", "\\sent", "\\drafts",
    "\\junk", "\\archive", "\\flagged", "\\important",
})
_LIST_RE = re.compile(rb'^\((?P<flags>[^)]*)\)\s+(?:"(?:[^"\\]|\\.)*"|NIL)\s+(?P<name>.+)$')

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _imap_date(days_ago: int) -> str:
    d = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return f"{d.day:02d}-{_MONTHS[d.month - 1]}-{d.year}"


def _check(resp: Any, what: str) -> list:
    status, data = resp
    if status != "OK":
        raise RuntimeError(f"IMAP {what} failed: {status} {data!r}")
    return data


class ImapMailStore:
    def __init__(
        self,
        conn: Any,
        account_hash: str,
        *,
        trash: str = "Trash",
        drafts: str = "Drafts",
        outbox: str = "Nucleon/Outbox",
        sent: str = "Sent",
    ) -> None:
        self._conn = conn
        self._account = account_hash
        self._trash = trash
        self._drafts = drafts
        self._outbox = outbox
        self._sent = sent
        self._system: Optional[frozenset[str]] = None

    # -- helpers -----------------------------------------------------------
    def system_folders(self) -> frozenset[str]:
        """Folders the provider flags as system/special (LIST attributes), plus
        the configured Trash/Drafts/Sent/Outbox and INBOX. Fails closed: if LIST
        fails, this raises and no label write proceeds. Cached per connection."""
        if self._system is None:
            names = {"INBOX", self._trash, self._drafts, self._sent, self._outbox}
            for line in _check(self._conn.list(), "LIST"):
                if not isinstance(line, bytes):
                    continue
                mt = _LIST_RE.match(line.strip())
                if not mt:
                    continue
                flags = {f.lower() for f in mt["flags"].decode("ascii", "replace").split()}
                if flags & _SYSTEM_FLAGS:
                    name = mt["name"].decode("utf-8", "replace").strip()
                    if len(name) >= 2 and name[0] == name[-1] == '"':
                        name = name[1:-1].replace('\\"', '"').replace("\\\\", "\\")
                    names.add(name)
            self._system = frozenset(names)
        return self._system

    def _select(self, mailbox: str) -> int:
        _check(self._conn.select(mailbox), f"SELECT {mailbox}")
        try:
            _, vals = self._conn.response("UIDVALIDITY")
            return int(vals[0])
        except (TypeError, ValueError, IndexError):
            return 0

    def _search(self, since_days: int, limit: int) -> list[int]:
        data = _check(
            self._conn.uid("SEARCH", None, "SINCE", _imap_date(since_days)), "SEARCH"
        )
        uids = [int(u) for u in (data[0] or b"").split()]
        return uids[-limit:] if limit > 0 else []

    def _fetch(self, uid: int, item: str) -> bytes:
        data = _check(self._conn.uid("FETCH", str(uid), item), "FETCH")
        for part in data:
            if isinstance(part, tuple):
                return part[1]
        return b""

    def _header_bytes(self, uid: int) -> bytes:
        return self._fetch(uid, "(BODY.PEEK[HEADER])")

    @staticmethod
    def _addrs(msg: Message, name: str) -> list[str]:
        return [a for _, a in getaddresses([str(v) for v in msg.get_all(name, [])]) if a]

    def _meta_from(self, ref: MessageRef, raw: bytes) -> MessageMeta:
        msg = message_from_bytes(raw, policy=_policy)
        date: Optional[datetime] = None
        if msg["Date"]:
            try:
                date = parsedate_to_datetime(str(msg["Date"]))
            except (TypeError, ValueError):
                date = None
        refs = re.findall(r"<[^>]+>", str(msg["References"] or ""))
        from_list = self._addrs(msg, "From")
        irt = str(msg["In-Reply-To"]).strip() if msg["In-Reply-To"] else None
        mid = str(msg["Message-ID"]).strip() if msg["Message-ID"] else None
        return MessageMeta(
            ref=ref,
            subject=str(msg["Subject"] or ""),
            from_addr=from_list[0] if from_list else "",
            to_addrs=self._addrs(msg, "To"),
            date=date,
            in_reply_to=irt,
            references=refs,
            message_id=mid,
        )

    def _build_ref(self, mailbox: str, uidvalidity: int, uid: int, raw: bytes) -> MessageRef:
        mid = message_from_bytes(raw, policy=_policy)["Message-ID"]
        return MessageRef.from_source(
            self._account, mailbox, uidvalidity, uid, str(mid).strip() if mid else None, raw
        )

    # -- MailStore ---------------------------------------------------------
    def list_window(self, mailbox: str, since_days: int, limit: int) -> list[MessageRef]:
        validity = self._select(mailbox)
        return [
            self._build_ref(mailbox, validity, uid, self._header_bytes(uid))
            for uid in self._search(since_days, limit)
        ]

    def fetch_meta(self, ref: MessageRef) -> MessageMeta:
        self._select(ref.mailbox)
        return self._meta_from(ref, self._header_bytes(ref.uid))

    def fetch_body_text(self, ref: MessageRef) -> str:
        self._select(ref.mailbox)
        msg = message_from_bytes(self._fetch(ref.uid, "(BODY.PEEK[])"), policy=_policy)
        part = msg.get_body(preferencelist=("plain", "html"))
        if part is None:
            return ""
        try:
            return part.get_content()
        except (LookupError, ValueError):
            return ""

    def list_sent(self, since_days: int, limit: int) -> list[MessageMeta]:
        validity = self._select(self._sent)
        out = []
        for uid in self._search(since_days, limit):
            raw = self._header_bytes(uid)
            out.append(self._meta_from(self._build_ref(self._sent, validity, uid, raw), raw))
        return out

    def append_draft(self, target: str, mime_bytes: bytes) -> MessageRef:
        folders = {"drafts": self._drafts, "outbox": self._outbox}
        if target not in folders:
            raise ValueError(f"unknown draft target: {target!r}")
        folder = folders[target]
        data = _check(self._conn.append(folder, "(\\Draft)", None, mime_bytes), "APPEND")
        text = b" ".join(d for d in data if isinstance(d, bytes)).decode("ascii", "replace")
        m = re.search(r"APPENDUID\s+(\d+)\s+(\d+)", text)
        validity, uid = (int(m.group(1)), int(m.group(2))) if m else (0, 0)  # best effort
        mid = message_from_bytes(mime_bytes, policy=_policy)["Message-ID"]
        return MessageRef.from_source(
            self._account, folder, validity, uid, str(mid).strip() if mid else None, mime_bytes
        )

    def set_label(self, ref: MessageRef, label: str) -> None:
        """Copy the message into the label folder (non-destructive).

        Proton Bridge exposes labels as folders; the exact label mechanism
        needs live verification against a real Bridge.
        """
        if label.casefold() in {n.casefold() for n in self.system_folders()}:
            raise ValueError("label resolves to a system/special folder")
        self._select(ref.mailbox)
        _check(self._conn.uid("COPY", str(ref.uid), label), "COPY")

    def move_to_trash(self, ref: MessageRef) -> None:
        """Targeted, reversible UID MOVE (RFC 6851) to Trash; never expunges/purges."""
        self._select(ref.mailbox)
        _check(self._conn.uid("MOVE", str(ref.uid), self._trash), "MOVE")
