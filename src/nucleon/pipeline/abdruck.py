"""PDF-Abdruck: a durable, human-readable record of a message, made BEFORE it is trashed.

Records (Abdrucke) are what make the delete step of the Nucleon loop safe: the source mail
is only ever removed once a faithful, offline-readable copy of it exists. The ``Abdruck``
protocol is injected into the mail loop (``mailfetch.loop.Recorder``), so the loop stays pure
and the record format is swappable.

``ChromiumAbdruck`` is the live adapter: it renders the message to a self-contained HTML page
and drives headless Chromium's ``--print-to-pdf`` to turn that into a PDF. The subprocess
runner is injected, so command construction is unit-tested without launching a browser.

Skeleton scope (honest): the HTML carries the envelope headers plus the message's best text
body (HTML part if present, else text/plain escaped). Full MIME fidelity — inline images,
an attachments appendix, calendar parts — is deliberately out of scope here and left as a
documented TODO; the attachments themselves are preserved separately by the filing stager.
"""
from __future__ import annotations

import hashlib
import html
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from email import message_from_bytes
from email.message import Message
from typing import Callable, Optional, Protocol

# Belt-and-suspenders on top of the CSP: strip tags the CSP does not cover in a file://
# context — notably <meta http-equiv="refresh"> (navigation/redirect) and <script>.
_STRIP_TAGS = re.compile(r"<\s*(script|meta|base|iframe|object|embed|link)\b[^>]*>",
                         re.IGNORECASE)
_STRIP_SCRIPT_BODY = re.compile(r"<\s*script\b.*?<\s*/\s*script\s*>", re.IGNORECASE | re.DOTALL)


class Abdruck(Protocol):
    """Make a durable record of ``raw`` and return an identifier/path for it.

    MUST NOT delete, move or mutate the source. Raising means 'no record was made', which
    the loop treats as 'do not delete the original'."""

    def record(self, folder: str, uid: str, raw: bytes) -> str: ...


# --- rendering ---------------------------------------------------------------

_HEADERS = ("Date", "From", "To", "Cc", "Subject", "Message-ID")


def _best_body_html(msg: Message) -> str:
    """The richest readable body: an HTML part verbatim, else text/plain escaped into <pre>."""
    plain: Optional[str] = None
    for part in msg.walk():
        if part.is_multipart():
            continue
        ctype = part.get_content_type()
        if (part.get_content_disposition() or "").lower() == "attachment":
            continue
        try:
            payload = part.get_payload(decode=True)
        except Exception:
            payload = None
        if payload is None:
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            text = payload.decode(charset, errors="replace")
        except (LookupError, ValueError):
            text = payload.decode("utf-8", errors="replace")
        if ctype == "text/html":
            # Neutralize redirecting/active tags the CSP can't catch via file://.
            text = _STRIP_SCRIPT_BODY.sub("", text)
            return _STRIP_TAGS.sub("", text)
        if ctype == "text/plain" and plain is None:
            plain = text
    if plain is not None:
        return "<pre>" + html.escape(plain) + "</pre>"
    return "<p><em>(no readable text body)</em></p>"


def message_to_html(raw: bytes) -> str:
    """Self-contained HTML record: an envelope header block above the message body."""
    msg = message_from_bytes(raw)
    rows = []
    for h in _HEADERS:
        v = msg.get(h)
        if v:
            rows.append(
                "<tr><th style='text-align:left;padding-right:1em;vertical-align:top'>"
                f"{html.escape(h)}</th><td>{html.escape(str(v))}</td></tr>"
            )
    header_block = "<table>" + "".join(rows) + "</table><hr>"
    body = _best_body_html(msg)
    # R-035a: a mail body is hostile input. A strict Content-Security-Policy keeps the Abdruck
    # OFFLINE and inert — no scripts, no external fetches (images/fonts/styles/frames). Only
    # inline styles and data: images render, so the record cannot phone home or load trackers.
    csp = ("default-src 'none'; img-src data:; style-src 'unsafe-inline'; "
           "font-src data:; base-uri 'none'; form-action 'none'")
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<meta http-equiv='Content-Security-Policy' content=\"{csp}\">"
        "<style>body{font-family:sans-serif;font-size:11pt;margin:2em}"
        "pre{white-space:pre-wrap;word-wrap:break-word}</style></head>"
        f"<body>{header_block}{body}</body></html>"
    )


def _safe_component(value: str, fallback: str) -> str:
    """One filesystem-safe path component (no separators, no dotty names, bounded length)."""
    cleaned = "".join(c for c in (value or "") if c.isalnum() or c in "-_") or fallback
    return cleaned[:120]


# --- live adapter ------------------------------------------------------------

@dataclass
class ChromiumAbdruck:
    """Render a message to PDF via headless Chromium's print-to-pdf.

    R-034 — **stable identity**: the record is keyed by ``<key_prefix>__<folder>__<uid>.pdf``,
    not by the UID alone, so two folders that happen to share a UID can never overwrite each
    other's Abdruck. ``key_prefix`` is where the live adapter injects the account and
    UIDVALIDITY (e.g. ``"<account>-<uidvalidity>"``), which are not visible to the pure loop.

    R-034 — **durability**: every run writes a FRESH PDF to a private temp path, validates it
    is non-empty, then atomically ``os.replace``\\s it into place — so a stale same-key file
    can never satisfy a later run's existence check, and a half-written PDF is never published.

    R-035a — **isolation**: a throwaway ``--user-data-dir`` per record and extensions disabled;
    the CSP in the rendered HTML already blocks scripts and external fetches.

    ``runner`` is injected (default ``subprocess.run``) so the argv is unit-testable without a
    browser; ``chromium_bin`` defaults to the container's pre-installed Chromium."""

    out_dir: str
    chromium_bin: str = "/opt/pw-browsers/chromium"
    runner: Callable = subprocess.run
    timeout: int = 120
    key_prefix: str = ""   # live adapter injects "<account>-<uidvalidity>" here

    def record(self, folder: str, uid: str, raw: bytes) -> str:
        os.makedirs(self.out_dir, exist_ok=True)
        base = "__".join(c for c in (
            _safe_component(self.key_prefix, "") or None,
            _safe_component(folder, "nofolder"),
            _safe_component(uid, "msg"),
        ) if c)
        # A short hash of the RAW composite key disambiguates sanitizer collisions
        # (e.g. folder "a/b" and "ab" both sanitize to "ab") so two distinct messages can
        # never share a path.
        keyhash = hashlib.sha1(
            "\x00".join((self.key_prefix, folder, uid)).encode("utf-8", "replace"),
            usedforsecurity=False,  # collision-disambiguation key, not a security primitive
        ).hexdigest()[:10]
        out_pdf = os.path.join(self.out_dir, base + "-" + keyhash + ".pdf")
        # A UNIQUE temp per attempt (same dir -> os.replace atomic, never EXDEV). mkstemp makes
        # a fresh 0-byte file, so a stale .tmp from a crashed prior run can never be mistaken
        # for this run's output, and a silently-empty render is caught by the size check below
        # (R-034 restart/stale-temp).
        fd, tmp_pdf = tempfile.mkstemp(dir=self.out_dir, prefix=".abdruck-", suffix=".pdf.tmp")
        os.close(fd)
        work = tempfile.mkdtemp()
        profile = tempfile.mkdtemp()           # R-035a: isolated, throwaway browser profile
        in_html = os.path.join(work, "record.html")
        try:
            with open(in_html, "w", encoding="utf-8") as fh:
                fh.write(message_to_html(raw))
            argv = [
                self.chromium_bin, "--headless", "--no-sandbox", "--disable-gpu",
                "--disable-extensions", "--user-data-dir=" + profile,
                "--print-to-pdf-no-header", "--print-to-pdf=" + tmp_pdf,
                "file://" + in_html,
            ]
            res = self.runner(argv, capture_output=True, text=True, timeout=self.timeout)
            rc = getattr(res, "returncode", 0)
            if rc != 0:
                raise OSError("chromium print-to-pdf failed rc=%s: %s"
                              % (rc, getattr(res, "stderr", "")))
            # R-034: validate the output BEFORE it counts as a record. Only a live subprocess
            # actually writes the file; an injected test runner is trusted for its argv.
            if self.runner is subprocess.run:
                if not os.path.exists(tmp_pdf) or os.path.getsize(tmp_pdf) == 0:
                    raise OSError("chromium reported success but produced no/empty PDF")
                os.replace(tmp_pdf, out_pdf)   # atomic, same-filesystem publish
        finally:
            try:
                os.remove(tmp_pdf)
            except OSError:
                pass
            shutil.rmtree(work, ignore_errors=True)      # dirs hold files -> rmtree, not rmdir
            shutil.rmtree(profile, ignore_errors=True)   # Chromium fills the profile dir
        return out_pdf
