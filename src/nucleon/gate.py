"""Deterministic gate: the security boundary between model output and actions.

Pure function over validated typed fields and config only. It never inspects
free text to decide which action types to produce, and only the three action
types in ``Action`` exist (no send, no permanent delete).
"""
from __future__ import annotations

from nucleon.config import Config
from nucleon.models import (
    Action,
    ApplyLabel,
    Classification,
    DeleteToTrash,
    MessageMeta,
    StageDraft,
)


def decide(classification: Classification, meta: MessageMeta, cfg: Config) -> list[Action]:
    actions: list[Action] = [ApplyLabel(ref=meta.ref, label=l) for l in classification.labels]

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
