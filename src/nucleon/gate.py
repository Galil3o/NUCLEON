"""Deterministic gate: the security boundary between model output and actions.

Pure function over validated typed fields and config only. It never inspects
free text to decide which action types to produce, and only the three action
types in ``Action`` exist (no send, no permanent delete).
"""
from __future__ import annotations

import re

from nucleon.config import Config
from nucleon.models import (
    Action,
    ApplyLabel,
    Classification,
    DeleteToTrash,
    MessageMeta,
    StageDraft,
)


_LABEL_RE = re.compile(r"^[A-Za-z0-9 _/.-]+$")


def _is_valid_label(label: str) -> bool:
    """Model-derived labels reach an IMAP COPY argument; validate here, not in imaplib."""
    if not label or len(label) > 64:
        return False
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in label):
        return False
    return _LABEL_RE.fullmatch(label) is not None


def decide(classification: Classification, meta: MessageMeta, cfg: Config) -> list[Action]:
    actions: list[Action] = [ApplyLabel(ref=meta.ref, label=l)
        for l in classification.labels
        if _is_valid_label(l)
    ]

    if classification.needs_reply and classification.draft_body:
        refs = list(meta.references)
        if meta.message_id and meta.message_id not in refs:
            refs.append(meta.message_id)
        actions.append(
            StageDraft(
                ref=meta.ref,
                subject=classification.draft_subject or ("Re: " + meta.subject),
                body=classification.draft_body,
                to=meta.from_addr,
                in_reply_to=meta.message_id,
                references=tuple(refs),
                target=cfg.draft.target,
                label=cfg.draft.label,
            )
        )

    if (
        cfg.cleanup.auto_trash_enabled
        and classification.category in cfg.cleanup.auto_trash_categories
    ):
        actions.append(DeleteToTrash(ref=meta.ref))

    return actions
