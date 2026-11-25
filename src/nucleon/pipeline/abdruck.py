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

import html
import os
import subprocess
import tempfile
from dataclasses import dataclass
from email import message_from_bytes
from email.message import Message
from typing import Callable, Optional, Protocol


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
            return text  # rendered as-is by Chromium
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
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<style>body{font-family:sans-serif;font-size:11pt;margin:2em}"
        "pre{white-space:pre-wrap;word-wrap:break-word}</style></head>"
        f"<body>{header_block}{body}</body></html>"
    )


# --- live adapter ------------------------------------------------------------

@dataclass
class ChromiumAbdruck:
    """Render a message to PDF via headless Chromium's print-to-pdf.

    ``out_dir`` receives ``<uid>.pdf``. ``runner`` is injected (default ``subprocess.run``)
    so the argv can be asserted in tests without a browser. ``chromium_bin`` defaults to the
    container's pre-installed Chromium; override for other hosts."""

    out_dir: str
    chromium_bin: str = "/opt/pw-browsers/chromium"
    runner: Callable = subprocess.run
    timeout: int = 120

    def record(self, folder: str, uid: str, raw: bytes) -> str:
        os.makedirs(self.out_dir, exist_ok=True)
        safe_uid = "".join(c for c in uid if c.isalnum() or c in "-_") or "msg"
        out_pdf = os.path.join(self.out_dir, safe_uid + ".pdf")
        work = tempfile.mkdtemp()
        in_html = os.path.join(work, "record.html")
        try:
            with open(in_html, "w", encoding="utf-8") as fh:
                fh.write(message_to_html(raw))
            argv = [
                self.chromium_bin, "--headless", "--no-sandbox", "--disable-gpu",
                "--print-to-pdf-no-header", "--print-to-pdf=" + out_pdf,
                "file://" + in_html,
            ]
            res = self.runner(argv, capture_output=True, text=True, timeout=self.timeout)
            rc = getattr(res, "returncode", 0)
            if rc != 0:
                raise OSError("chromium print-to-pdf failed rc=%s: %s"
                              % (rc, getattr(res, "stderr", "")))
            # A runner that reports success but produced no file is still a failed record.
            if self.runner is subprocess.run and not os.path.exists(out_pdf):
                raise OSError("chromium reported success but no PDF was written")
        finally:
            try:
                os.remove(in_html); os.rmdir(work)
            except OSError:
                pass
        return out_pdf
