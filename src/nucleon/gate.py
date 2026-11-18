"""Deterministic gate: the security boundary between model output and actions.

Pure function over validated typed fields and config only. It never inspects
free text to decide which action types to produce, and only the three action
types in ``Action`` exist (no send, no permanent delete).
"""
from __future__ import annotations

import re
from collections.abc import Iterable

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


# Well-known system/special folder names and aliases (casefolded). Provider
# metadata (``system_folders``) is the authority; this is a static backstop.
_SYSTEM_NAMES = frozenset({
    "inbox", "trash", "bin", "deleted", "deleted items", "deleted messages",
    "sent", "sent items", "sent mail", "sent messages", "drafts", "draft",
    "junk", "junk e-mail", "junk email", "spam", "archive", "archives",
    "all mail", "all", "starred", "flagged", "important", "outbox",
})


def _label_allowed(label: str, cfg: Config, system: frozenset[str]) -> bool:
    """R-001: namespace or approved target, never a system/special folder."""
    if not _is_valid_label(label):
        return False
    segments = label.split("/")
    if any(s in ("", ".", "..") for s in segments):
        return False
    folded = label.casefold()
    if folded in system or folded in _SYSTEM_NAMES:
        return False
    ns = cfg.labels.namespace
    in_namespace = label.startswith(ns) and len(label) > len(ns)
    return in_namespace or label in cfg.labels.allowed


def decide(
    classification: Classification,
    meta: MessageMeta,
    cfg: Config,
    system_folders: Iterable[str] = (),
) -> list[Action]:
    system = frozenset(f.casefold() for f in system_folders)
    actions: list[Action] = [ApplyLabel(ref=meta.ref, label=l)
        for l in classification.labels
        if _label_allowed(l, cfg, system)
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
