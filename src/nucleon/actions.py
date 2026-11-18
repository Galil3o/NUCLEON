"""Actions executor: applies gate-approved actions to a MailStore.

Only the three types in ``Action`` exist. There is no send path and removal
is Trash-only.
"""
from __future__ import annotations

import hashlib
from collections.abc import Callable
from email.message import EmailMessage
from typing import Optional

from nucleon.mailstore import MailStore
from nucleon.models import Action, ApplyLabel, DeleteToTrash, DraftRef, MessageRef, StageDraft


def draft_message_id(processing_key: str) -> str:
    """Deterministic Message-ID of the draft staged for a source message (R-004).

    Derived from the source's processing key, so it can be recomputed (to look
    the draft up, or to recognise it in Sent) without persisting plaintext.
    """
    return f"<nucleon-{hashlib.sha256(processing_key.encode()).hexdigest()[:32]}@nucleon.invalid>"


def message_id_hash(message_id: str) -> str:
    return hashlib.sha256(message_id.encode()).hexdigest()


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
        if action.to is not None:
            msg["To"] = action.to
        if action.in_reply_to is not None:
            msg["In-Reply-To"] = action.in_reply_to
        if action.references:
            msg["References"] = " ".join(action.references)
        msg.set_content(action.body)
        mhash = message_id_hash(mid)
        if on_draft_intent is not None:
            on_draft_intent(DraftRef(target=action.target, message_id_hash=mhash))
        ref = store.append_draft(action.target, msg.as_bytes())
        if on_draft_appended is not None:
            on_draft_appended(
                DraftRef(
                    target=action.target,
                    message_id_hash=mhash,
                    confirmed=ref.uidvalidity > 0 and ref.uid > 0,
                    mailbox=ref.mailbox,
                    uidvalidity=ref.uidvalidity,
                    uid=ref.uid,
                )
            )
        if action.label is not None:
            store.set_label(ref, action.label)
        return ref
    elif isinstance(action, DeleteToTrash):
        store.move_to_trash(action.ref)
        return None
    else:  # pragma: no cover - Action is a closed union
        raise TypeError(f"unsupported action: {type(action).__name__}")
