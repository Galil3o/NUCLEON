"""IMAP MailStore adapter over an ``imaplib.IMAP4``-shaped connection.

Safety rules: reads use BODY.PEEK only (never sets \\Seen); there is no send
path; delete is a targeted, reversible UID MOVE to Trash (never EXPUNGE/purge).
Live Proton Bridge acceptance is deferred (post-v0.1).
"""
from __future__ import annotations

import base64
import binascii
import quopri
import re
from datetime import datetime, timedelta, timezone
from email import message_from_bytes
from email.message import Message
from email.policy import default as _policy
from email.utils import getaddresses, parsedate_to_datetime
from typing import Any, Iterator, Optional

from nucleon.budgets import (
    DEFAULT_MAX_BODY_BYTES,
    DEFAULT_MAX_HEADER_BYTES,
    DEFAULT_MAX_MESSAGE_BYTES,
    BudgetExceeded,
)
from nucleon.mailstore import BodyFetch, PreflightReport, PreflightRequest, StaleReferenceError
from nucleon.models import MessageMeta, MessageRef

# LIST attributes (RFC 3501 / RFC 6154 SPECIAL-USE) that mark a folder as
# non-label: unselectable containers and system/special-use folders.
_SYSTEM_FLAGS = frozenset({
    "\\noselect", "\\nonexistent", "\\all", "\\trash", "\\sent", "\\drafts",
    "\\junk", "\\archive", "\\flagged", "\\important",
})
_LIST_RE = re.compile(rb'^\((?P<flags>[^)]*)\)\s+(?:"(?:[^"\\]|\\.)*"|NIL)\s+(?P<name>.+)$')

_NS_RE = re.compile(rb'^\(\(\s*"((?:[^"\\]|\\.)*)"\s+(?:"((?:[^"\\]|\\.)*)"|NIL)')
_LIST_DELIM_RE = re.compile(rb'^\((?:[^)]*)\)\s+(?:"((?:[^"\\]|\\.)*)"|NIL)\s')

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _imap_date(days_ago: int) -> str:
    d = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return f"{d.day:02d}-{_MONTHS[d.month - 1]}-{d.year}"


def _check(resp: Any, what: str) -> list:
    status, data = resp
    if status != "OK":
        raise RuntimeError(f"IMAP {what} failed: {status} {data!r}")
    return data


_TOKEN_RE = re.compile(rb'\s*(?:(\()|(\))|"((?:[^"\\]|\\.)*)"|([^\s()"]+))')


def _parse_sexpr(data: bytes, start: int) -> list:
    """Parse one parenthesised IMAP list (BODYSTRUCTURE) into nested lists.

    Strings become ``str``, NIL ``None``, bare atoms ``str``/``int``. Anything
    malformed raises ValueError: the caller then fails closed instead of
    falling back to loading the whole message.
    """
    pos = start
    stack: list[list] = []
    root: list | None = None
    while True:
        m = _TOKEN_RE.match(data, pos)
        if not m:
            raise ValueError("unparseable BODYSTRUCTURE")
        pos = m.end()
        if m.group(1) is not None:
            new: list = []
            if stack:
                stack[-1].append(new)
            else:
                root = new
            stack.append(new)
        elif m.group(2) is not None:
            if not stack:
                raise ValueError("unparseable BODYSTRUCTURE")
            stack.pop()
            if not stack:
                return root if root is not None else []
        else:
            if not stack:
                raise ValueError("unparseable BODYSTRUCTURE")
            if m.group(3) is not None:
                val: Any = m.group(3).decode("utf-8", "replace")
            else:
                atom = m.group(4)
                val = None if atom.upper() == b"NIL" else (
                    int(atom) if atom.isdigit() else atom.decode("utf-8", "replace"))
            stack[-1].append(val)


def _is_attachment(part: list) -> bool:
    return any(
        isinstance(x, list) and x and isinstance(x[0], str) and x[0].lower() == "attachment"
        for x in part
    )


def _text_parts(node: list, prefix: str, out: list[tuple[str, str, list]]) -> None:
    """Collect (part-number, subtype, part) for inline text/* leaves, in order."""
    if node and isinstance(node[0], list):  # multipart: children first
        n = 0
        for child in node:
            if not isinstance(child, list):
                break
            n += 1
            _text_parts(child, f"{prefix}.{n}" if prefix else str(n), out)
        return
    if len(node) >= 7 and isinstance(node[0], str) and node[0].lower() == "text" \
            and isinstance(node[1], str) and not _is_attachment(node):
        out.append((prefix or "1", node[1].lower(), node))


def _pick_text_part(structure: list) -> tuple[str, list] | None:
    found: list[tuple[str, str, list]] = []
    _text_parts(structure, "", found)
    for want in ("plain", "html"):
        for number, subtype, part in found:
            if subtype == want:
                return number, part
    return None


def _decode_part(data: bytes, part: list) -> str:
    params = part[2] if isinstance(part[2], list) else []
    charset = "utf-8"
    for k, v in zip(params[0::2], params[1::2]):
        if isinstance(k, str) and k.lower() == "charset" and isinstance(v, str):
            charset = v
    enc = (part[5] or "7bit").lower() if isinstance(part[5], str) else "7bit"
    if enc == "base64":
        clean = re.sub(rb"[^A-Za-z0-9+/=]", b"", data)
        clean = clean[: len(clean) - len(clean) % 4]  # a cut mid-quantum is expected
        try:
            data = base64.b64decode(clean)
        except (binascii.Error, ValueError):
            data = b""
    elif enc == "quoted-printable":
        data = quopri.decodestring(data)
    try:
        return data.decode(charset, "replace")
    except LookupError:
        return data.decode("utf-8", "replace")


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
        max_message_bytes: int = DEFAULT_MAX_MESSAGE_BYTES,
        max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
        max_header_bytes: int = DEFAULT_MAX_HEADER_BYTES,
    ) -> None:
        self._max_message = max_message_bytes
        self._max_body = max_body_bytes
        self._max_header = max_header_bytes
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

    def _select_checked(self, ref: MessageRef) -> None:
        """(Re-)SELECT and verify UIDVALIDITY before any UID-based access (R-002)."""
        validity = self._select(ref.mailbox)
        if validity == 0 or validity != ref.uidvalidity:
            raise StaleReferenceError(ref.mailbox)

    def _select(self, mailbox: str, readonly: bool = False) -> int:
        resp = self._conn.select(mailbox, readonly=True) if readonly else self._conn.select(mailbox)
        _check(resp, f"SELECT {mailbox}")
        try:
            _, vals = self._conn.response("UIDVALIDITY")
            return int(vals[0])
        except (TypeError, ValueError, IndexError):
            return 0

    def _search_all(self, since_days: int) -> list[int]:
        """All matching UIDs, ascending (stable order); no windowing."""
        data = _check(
            self._conn.uid("SEARCH", None, "SINCE", _imap_date(since_days)), "SEARCH"
        )
        return sorted(int(u) for u in (data[0] or b"").split())

    def _search(self, since_days: int, limit: int) -> list[int]:
        uids = self._search_all(since_days)
        return uids[-limit:] if limit > 0 else []

    def _fetch(self, uid: int, item: str) -> bytes:
        data = _check(self._conn.uid("FETCH", str(uid), item), "FETCH")
        for part in data:
            if isinstance(part, tuple):
                return part[1]
        return b""

    def _header_bytes(self, uid: int) -> bytes:
        # Byte-limited (R-011): an enormous header block is never loaded whole.
        return self._fetch(uid, f"(BODY.PEEK[HEADER]<0.{self._max_header}>)")

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
            cc_addrs=self._addrs(msg, "Cc"),
            reply_to=(self._addrs(msg, "Reply-To") or [None])[0],
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

    # -- preflight (R-010) -------------------------------------------------
    def _capabilities(self) -> frozenset[str]:
        data = _check(self._conn.capability(), "CAPABILITY")
        text = b" ".join(d for d in data if isinstance(d, bytes)).decode("ascii", "replace")
        return frozenset(t.upper() for t in text.split())

    def _namespace(self, caps: frozenset[str]) -> tuple[str, str | None]:
        """(personal prefix, hierarchy delimiter). NAMESPACE if offered, else the
        delimiter from ``LIST "" ""``; an unparseable answer yields ("", None)."""
        if "NAMESPACE" in caps:
            data = _check(self._conn.namespace(), "NAMESPACE")
            raw = next((d for d in data if isinstance(d, bytes)), b"")
            m = _NS_RE.match(raw.strip())
            if m:
                return (m.group(1).decode("utf-8", "replace"),
                        m.group(2).decode("utf-8", "replace") if m.group(2) is not None else None)
            return "", None
        for line in _check(self._conn.list('""', '""'), "LIST"):
            m = _LIST_DELIM_RE.match(line.strip()) if isinstance(line, bytes) else None
            if m and m.group(1) is not None:
                return "", m.group(1).decode("utf-8", "replace")
        return "", None

    def _folder_names(self) -> set[str]:
        names: set[str] = set()
        for line in _check(self._conn.list(), "LIST"):
            mt = _LIST_RE.match(line.strip()) if isinstance(line, bytes) else None
            if not mt:
                continue
            name = mt["name"].decode("utf-8", "replace").strip()
            if len(name) >= 2 and name[0] == name[-1] == '"':
                name = name[1:-1].replace('\\"', '"').replace("\\\\", "\\")
            names.add(name)
        return names

    def preflight(self, req: PreflightRequest) -> PreflightReport:
        """R-010: verify capabilities, namespace and target folders BEFORE the
        first write. Read-only (CAPABILITY / NAMESPACE / LIST) unless the
        operator opted into ``create_missing``; never creates system folders and
        never guesses a name translation. Raises if the server cannot answer."""
        problems: list[str] = []
        warnings: list[str] = []
        created: list[str] = []
        caps = self._capabilities()
        if "IMAP4REV1" not in caps:
            problems.append("server does not advertise IMAP4rev1")
        if req.need_move and "MOVE" not in caps:
            problems.append("capability MOVE (RFC 6851) required for Trash moves is missing")
        if "UIDPLUS" not in caps:
            warnings.append("no UIDPLUS: APPENDUID unavailable; drafts are located by "
                            "Message-ID SEARCH after APPEND")
        prefix, delim = self._namespace(caps)
        existing = {n.casefold() for n in self._folder_names()}
        system = {self._trash, self._drafts, self._sent, "INBOX"}
        wanted: list[str] = []
        folders = {"drafts": self._drafts, "outbox": self._outbox}
        for t in req.draft_targets:
            if t not in folders:
                problems.append(f"unknown draft target {t!r}")
            else:
                wanted.append(folders[t])
        wanted.extend(req.folders)
        if req.need_move:
            wanted.append(self._trash)
        for name in dict.fromkeys(wanted):
            if name.casefold() in existing:
                continue
            if delim is not None and delim != "/" and "/" in name:
                problems.append(
                    f"folder {name!r} missing and its '/' does not match the server "
                    f"hierarchy delimiter {delim!r}; not translating names")
            elif prefix and not name.startswith(prefix):
                problems.append(
                    f"folder {name!r} missing and lies outside the personal namespace "
                    f"{prefix!r}")
            elif name in system or name.casefold() in {s.casefold() for s in system}:
                problems.append(f"system folder {name!r} is missing; never created automatically")
            elif req.create_missing:
                _check(self._conn.create(name), f"CREATE {name}")
                created.append(name)
            else:
                problems.append(
                    f"folder {name!r} is missing (set draft.create_missing_folders to "
                    "let Nucleon create it)")
        return PreflightReport(tuple(problems), tuple(warnings), tuple(created))

    # -- MailStore ---------------------------------------------------------
    def list_window(self, mailbox: str, since_days: int, limit: int) -> list[MessageRef]:
        validity = self._select(mailbox)
        return [
            self._build_ref(mailbox, validity, uid, self._header_bytes(uid))
            for uid in self._search(since_days, limit)
        ]

    def iter_window(self, mailbox: str, since_days: int) -> Iterator[MessageRef]:
        """R-005: every UID in the window, oldest first, headers fetched lazily.

        The caller (engine) applies its processing limit AFTER its marker /
        journal filter, so older unprocessed mail is never starved by newer
        mail. UIDVALIDITY is re-verified before each header fetch; if it
        changes mid-iteration the iteration ends (nothing is guessed).
        """
        validity = self._select(mailbox)
        for uid in self._search_all(since_days):
            if self._select(mailbox) != validity:
                return
            yield self._build_ref(mailbox, validity, uid, self._header_bytes(uid))

    def fetch_meta(self, ref: MessageRef) -> MessageMeta:
        self._select_checked(ref)
        return self._meta_from(ref, self._header_bytes(ref.uid))

    def fetch_body(self, ref: MessageRef) -> BodyFetch:
        """R-011: size + MIME structure first, then ONLY the text part, byte-limited."""
        self._select_checked(ref)
        data = _check(
            self._conn.uid("FETCH", str(ref.uid), "(RFC822.SIZE BODYSTRUCTURE)"), "FETCH"
        )
        blob = b" ".join(
            (p[0] if isinstance(p, tuple) else p) for p in data if isinstance(p, (bytes, tuple))
        )
        size_m = re.search(rb"RFC822\.SIZE\s+(\d+)", blob)
        bs_m = re.search(rb"BODYSTRUCTURE\s+\(", blob)
        if not size_m or not bs_m:
            raise ValueError("server did not report size/structure")
        size = int(size_m.group(1))
        if size > self._max_message:
            raise BudgetExceeded("message_size", self._max_message, size)
        picked = _pick_text_part(_parse_sexpr(blob, bs_m.end() - 1))
        if picked is None:
            return BodyFetch("", False, 0)
        number, part = picked
        part_size = part[6] if isinstance(part[6], int) else None
        raw = self._fetch(ref.uid, f"(BODY.PEEK[{number}]<0.{self._max_body}>)")[: self._max_body]
        truncated = part_size is not None and part_size > self._max_body
        return BodyFetch(_decode_part(raw, part), truncated, part_size)

    def fetch_body_text(self, ref: MessageRef) -> str:
        return self.fetch_body(ref).text

    def list_sent(self, since_days: int, limit: int) -> list[MessageMeta]:
        validity = self._select(self._sent)
        out = []
        for uid in self._search(since_days, limit):
            raw = self._header_bytes(uid)
            out.append(self._meta_from(self._build_ref(self._sent, validity, uid, raw), raw))
        return out

    def find_draft(self, target: str, message_id: str) -> list[MessageRef]:
        """Read-only (EXAMINE + SEARCH + BODY.PEEK) lookup by Message-ID (R-004)."""
        folders = {"drafts": self._drafts, "outbox": self._outbox}
        if target not in folders:
            raise ValueError(f"unknown draft target: {target!r}")
        folder = folders[target]
        validity = self._select(folder, readonly=True)
        data = _check(
            self._conn.uid("SEARCH", None, "HEADER", "Message-ID", message_id), "SEARCH"
        )
        uids = sorted(int(u) for u in (data[0] or b"").split())
        return [self._build_ref(folder, validity, u, self._header_bytes(u)) for u in uids]

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
        self._select_checked(ref)
        _check(self._conn.uid("COPY", str(ref.uid), label), "COPY")

    def move_to_trash(self, ref: MessageRef) -> None:
        """Targeted, reversible UID MOVE (RFC 6851) to Trash; never expunges/purges."""
        self._select_checked(ref)
        _check(self._conn.uid("MOVE", str(ref.uid), self._trash), "MOVE")
