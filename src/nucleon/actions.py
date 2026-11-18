"""Actions executor: applies gate-approved actions to a MailStore.

Only the three types in ``Action`` exist. There is no send path and removal
is Trash-only.
"""
from __future__ import annotations

import hashlib
import html
from collections.abc import Callable
from email.message import EmailMessage
from typing import Optional

from nucleon.mailstore import MailStore
from nucleon.models import Action, ApplyLabel, DeleteToTrash, DraftRef, MessageRef, StageDraft


class DraftLocationUnresolved(Exception):
    """The APPEND succeeded but the draft could not be located unambiguously
    (no APPENDUID, and the Message-ID SEARCH found none or several). No follow-up
    operation is applied to it; the unconfirmed reference stays in the marker."""


def draft_message_id(processing_key: str) -> str:
    """Deterministic Message-ID of the draft staged for a source message (R-004).

    Derived from the source's processing key, so it can be recomputed (to look
    the draft up, or to recognise it in Sent) without persisting plaintext.
    """
    return f"<nucleon-{hashlib.sha256(processing_key.encode()).hexdigest()[:32]}@nucleon.invalid>"


def message_id_hash(message_id: str) -> str:
    return hashlib.sha256(message_id.encode()).hexdigest()


_SOURCE_LABEL = {
    "de": {"from": "Absender", "reply-to": "Reply-To", "original-recipient": "urspr. Empfänger",
           "contact": "Kontakt", "learned": "gelernt"},
    "en": {"from": "sender", "reply-to": "Reply-To", "original-recipient": "original recipient",
           "contact": "contact", "learned": "learned"},
}
_TEXT = {
    "de": {"head": "ACHTUNG: ABWEICHENDE EMPFÄNGER", "to": "An", "cc": "Cc",
           "review": "Zur Prüfung, NICHT als Empfänger eingetragen",
           "del": "VOR DEM SENDEN LÖSCHEN / DELETE BEFORE SENDING", "end": "ENDE DES HINWEISES"},
    "en": {"head": "WARNING: DIFFERENT RECIPIENTS", "to": "To", "cc": "Cc",
           "review": "For review, NOT added as a recipient",
           "del": "VOR DEM SENDEN LÖSCHEN / DELETE BEFORE SENDING", "end": "END OF NOTICE"},
}


def _banner_lines(action: StageDraft, lang: str) -> list[str]:
    """Generated from the SAME recipient lines that become the headers (R-009)."""
    t, lab = _TEXT[lang], _SOURCE_LABEL[lang]
    lines = [f"⚠ {t['head']} ⚠"]
    for role in ("to", "cc"):
        picked = [r for r in action.recipients if r.role == role]
        if picked:
            lines.append(f"{t[role]}: " + ", ".join(f"{r.address} ({lab[r.source]})" for r in picked))
    if action.review_required:
        lines.append(f"{t['review']}: " + ", ".join(action.review_required))
    lines.append(f"— {t['del']} —")
    lines.append(f"⚠ {t['end']} ⚠")
    return lines


def _banner_html(lines: list[str]) -> str:
    inner = "<br>".join(html.escape(l) for l in lines)
    return ('<div style="background-color:#ffff00;color:#c00000;font-weight:bold;'
            f'padding:8px;border:2px solid #c00000">{inner}</div>')


def execute(
    action: Action,
    store: MailStore,
    *,
    on_draft_intent: Optional[Callable[[DraftRef], None]] = None,
    on_draft_appended: Optional[Callable[[DraftRef], None]] = None,
) -> Optional[MessageRef]:
    """Apply one action. For a StageDraft the callbacks fire (a) BEFORE the
    APPEND with an unconfirmed reference and (b) right AFTER it, before the
    draft label is applied, so the caller can persist them immediately."""
    if isinstance(action, ApplyLabel):
        store.set_label(action.ref, action.label)
        return None
    elif isinstance(action, StageDraft):
        msg = EmailMessage()
        mid = draft_message_id(action.ref.processing_key())
        msg["Message-ID"] = mid
        msg["Subject"] = action.subject
        if action.recipients:
            to_hdr = ", ".join(r.address for r in action.recipients if r.role == "to")
            cc_hdr = ", ".join(r.address for r in action.recipients if r.role == "cc")
        else:
            to_hdr, cc_hdr = action.to, ""
        if to_hdr:
            msg["To"] = to_hdr
        if cc_hdr:
            msg["Cc"] = cc_hdr
        if action.in_reply_to is not None:
            msg["In-Reply-To"] = action.in_reply_to
        if action.references:
            msg["References"] = " ".join(action.references)
        if action.banner is not None:
            blines = _banner_lines(action, action.banner)
            msg.set_content("\n".join(blines) + "\n\n" + action.body)  # plain keeps ⚠ … ⚠
            msg.add_alternative(
                "<html><body>" + _banner_html(blines)
                + '<div style="white-space:pre-wrap">' + html.escape(action.body)
                + "</div></body></html>", subtype="html")
        else:
            msg.set_content(action.body)
        mhash = message_id_hash(mid)
        if on_draft_intent is not None:
            on_draft_intent(DraftRef(target=action.target, message_id_hash=mhash))
        ref = store.append_draft(action.target, msg.as_bytes())

        def _dref(r: MessageRef, confirmed: bool) -> DraftRef:
            return DraftRef(target=action.target, message_id_hash=mhash, confirmed=confirmed,
                            mailbox=r.mailbox, uidvalidity=r.uidvalidity, uid=r.uid)

        located = ref.uidvalidity > 0 and ref.uid > 0
        if on_draft_appended is not None:
            on_draft_appended(_dref(ref, located))
        if not located:
            # R-010: APPEND succeeded but the server reported no APPENDUID. A ref
            # with uid 0 is NOT a valid reference: look the draft up by its
            # deterministic Message-ID, and only continue on a unique hit.
            found = [r for r in store.find_draft(action.target, mid)
                     if r.uidvalidity > 0 and r.uid > 0]
            if len(found) != 1:
                raise DraftLocationUnresolved(
                    f"draft appended to {action.target!r} but {len(found)} match(es) "
                    "found by Message-ID; not labelling"
                )
            ref = found[0]
            if on_draft_appended is not None:
                on_draft_appended(_dref(ref, True))
        if action.label is not None:
            store.set_label(ref, action.label)
        return ref
    elif isinstance(action, DeleteToTrash):
        store.move_to_trash(action.ref)
        return None
    else:  # pragma: no cover - Action is a closed union
        raise TypeError(f"unsupported action: {type(action).__name__}")
