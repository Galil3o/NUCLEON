"""Size/count budgets (R-011): defaults, shared types and the action cap.

Every budget is configurable (``limits:`` in config). A hard overrun REJECTS
that one message (``BudgetExceeded``); a soft overrun TRUNCATES and the message
is still processed. Either way a ``BudgetEvent`` (kind, limit, observed and a
pseudonymised reference; never content) is reported per message and the run
carries on.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_MAX_MESSAGE_BYTES = 10_000_000
DEFAULT_MAX_BODY_BYTES = 262_144
DEFAULT_MAX_HEADER_BYTES = 65_536
DEFAULT_MAX_MODEL_RESPONSE_BYTES = 1_048_576
DEFAULT_MAX_MODEL_INPUT_CHARS = 32_000
DEFAULT_MAX_LABELS = 10
DEFAULT_MAX_ACTIONS = 20

BudgetKind = Literal[
    "message_size", "body_bytes", "model_input", "model_response", "labels", "actions",
]


class BudgetExceeded(Exception):
    """A hard size budget was exceeded; the message is rejected, the run goes on."""

    def __init__(self, kind: BudgetKind | str, limit: int, observed: int) -> None:
        super().__init__(f"{kind}: {observed} > {limit}")
        self.kind = kind
        self.limit = limit
        self.observed = observed


class BudgetEvent(BaseModel):
    """One visible budget overrun for one message. Numbers and a pseudonym only."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: BudgetKind
    outcome: Literal["rejected", "truncated"]
    limit: int = Field(ge=0)
    observed: int = Field(ge=0)
    ref_pseudonym: str = Field(pattern=r"^[0-9a-f]{16}$")


def cap_actions(acts: list, max_labels: int, max_actions: int):
    """Bound labels, then total actions. Returns (acts, [(kind, limit, observed)]).

    Order is preserved (labels, draft, trash), so a cut at the tail drops the
    later, higher-impact actions first rather than the cheap reversible labels.
    """
    from nucleon.models import ApplyLabel

    over: list[tuple[str, int, int]] = []
    n_labels = sum(isinstance(a, ApplyLabel) for a in acts)
    if n_labels > max_labels:
        over.append(("labels", max_labels, n_labels))
        kept, seen = [], 0
        for a in acts:
            if isinstance(a, ApplyLabel):
                seen += 1
                if seen > max_labels:
                    continue
            kept.append(a)
        acts = kept
    if len(acts) > max_actions:
        over.append(("actions", max_actions, len(acts)))
        acts = acts[:max_actions]
    return acts, over
