"""Actions executor: applies gate-approved actions to a MailStore.

Only the three types in ``Action`` exist. There is no send path and removal
is Trash-only.
"""
from __future__ import annotations

from email.message import EmailMessage

from nucleon.mailstore import MailStore
from nucleon.models import Action, ApplyLabel, DeleteToTrash, StageDraft


def execute(action: Action, store: MailStore) -> None:
    if isinstance(action, ApplyLabel):
        store.set_label(action.ref, action.label)
    elif isinstance(action, StageDraft):
        msg = EmailMessage()
        msg["Subject"] = action.subject
        if action.to is not None:
            msg["To"] = action.to
        if action.in_reply_to is not None:
            msg["In-Reply-To"] = action.in_reply_to
        if action.references:
            msg["References"] = " ".join(action.references)
        msg.set_content(action.body)
        ref = store.append_draft(action.target, msg.as_bytes())
        if action.label is not None:
            store.set_label(ref, action.label)
    elif isinstance(action, DeleteToTrash):
        store.move_to_trash(action.ref)
    else:  # pragma: no cover - Action is a closed union
        raise TypeError(f"unsupported action: {type(action).__name__}")
