"""Error analysis (DECISIONS section 5, R-008).

Stage 1 is the per-message error the engine already isolates (journal/marker
status ``error``, no content). Stage 2 (``analysis.enabled``) writes ONE fixed
METADATA schema record per failed message: error code, failing pipeline stage,
duration and pseudonymised references. By construction it can carry NO mail
content: every field is a number, a pseudonym matching a hex pattern, or a
member of a closed enumeration, and unknown fields are rejected. In
particular there is no exception text, no traceback, no raw IMAP server reply
and no model output text, because any of these may echo untrusted content.

Stage 3 (content) is never automatic: it needs ``analysis.verbose`` AND a
separate, explicit, per-message, short-lived ``Stage3Confirmation``; its
records carry an expiry and are purged promptly. The engine never calls it.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field

from nucleon.appendlog import append_line
from nucleon.quarantine import ClassifierOutputError

FailedStage = Literal["fetch", "classify", "gate", "act"]
ErrorCode = Literal[
    "classifier_output_invalid", "budget_exceeded", "io_error", "value_error",
    "lookup_error", "runtime_error", "unexpected_error",
]
StepName = Literal["ApplyLabel", "StageDraft", "StageDraft:appended", "DeleteToTrash"]

_PSEUDONYM = r"^[0-9a-f]{16}$"
MAX_STAGE3_TTL_SECONDS = 3600


def pseudonym(value: str) -> str:
    """Stable, non-reversible short reference (inputs are already hashes/keys)."""
    return hashlib.sha256(b"nucleon-analysis:" + value.encode()).hexdigest()[:16]


def error_code_for(exc: BaseException) -> ErrorCode:
    """Map an exception to a closed code by TYPE only; its text is never read."""
    from nucleon.budgets import BudgetExceeded

    if isinstance(exc, BudgetExceeded):
        return "budget_exceeded"
    if isinstance(exc, ClassifierOutputError):
        return "classifier_output_invalid"
    if isinstance(exc, OSError):  # includes TimeoutError, socket and HTTP errors
        return "io_error"
    if isinstance(exc, ValueError):
        return "value_error"
    if isinstance(exc, LookupError):
        return "lookup_error"
    if isinstance(exc, RuntimeError):  # e.g. IMAP command failures
        return "runtime_error"
    return "unexpected_error"


class Stage2Record(BaseModel):
    """The ONLY thing Stage 2 may persist. Metadata schema, no content."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    analysis_stage: Literal[2] = 2
    failed_stage: FailedStage
    error_code: ErrorCode
    duration_ms: int = Field(ge=0)
    ref_pseudonym: str = Field(pattern=_PSEUDONYM)
    account_pseudonym: str = Field(pattern=_PSEUDONYM)
    steps_completed: tuple[StepName, ...] = ()
    ts: datetime


class AnalysisLog:
    """Append-only local JSONL of Stage 2 records."""

    def __init__(self, path: str) -> None:
        self._path = path

    def record_stage2(
        self,
        *,
        failed_stage: FailedStage,
        exc: BaseException,
        duration_ms: int,
        ref_key: str,
        account_hash: str,
        steps: tuple[str, ...] = (),
    ) -> Stage2Record:
        rec = Stage2Record(
            failed_stage=failed_stage,
            error_code=error_code_for(exc),
            duration_ms=max(0, duration_ms),
            ref_pseudonym=pseudonym(ref_key),
            account_pseudonym=pseudonym(account_hash),
            steps_completed=tuple(steps),  # type: ignore[arg-type]
            ts=datetime.now(timezone.utc),
        )
        append_line(self._path, rec.model_dump_json())  # R-013
        return rec


class Stage3NotConfirmed(Exception):
    """Content capture was attempted without a valid, explicit Stage-3 confirmation."""


class Stage3Confirmation(BaseModel):
    """Explicit operator confirmation for ONE message, valid for a short time."""

    model_config = ConfigDict(frozen=True)

    ref_pseudonym: str = Field(pattern=_PSEUDONYM)
    expires_at: datetime

    @classmethod
    def explicit(cls, ref_pseudonym: str, *, ttl_seconds: int, now: datetime) -> "Stage3Confirmation":
        if not 0 < ttl_seconds <= MAX_STAGE3_TTL_SECONDS:
            raise ValueError(f"ttl_seconds must be in 1..{MAX_STAGE3_TTL_SECONDS}")
        return cls(ref_pseudonym=ref_pseudonym, expires_at=now + timedelta(seconds=ttl_seconds))


class Stage3Store:
    """Local, short-lived content records; only reachable with a confirmation."""

    def __init__(
        self,
        path: str,
        *,
        verbose_enabled: bool,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._path = path
        self._verbose = verbose_enabled
        self._clock = clock

    def write(
        self,
        confirmation: Stage3Confirmation | None,
        content: dict[str, str],
        ref_pseudonym: str | None = None,
    ) -> None:
        now = self._clock()
        if not self._verbose:
            raise Stage3NotConfirmed("analysis.verbose is off")
        if confirmation is None:
            raise Stage3NotConfirmed("no Stage-3 confirmation")
        if confirmation.expires_at <= now:
            raise Stage3NotConfirmed("Stage-3 confirmation expired")
        ref = ref_pseudonym or confirmation.ref_pseudonym
        if ref != confirmation.ref_pseudonym:
            raise Stage3NotConfirmed("confirmation is for a different message")
        line = json.dumps({"ref_pseudonym": ref, "expires_at": confirmation.expires_at.isoformat(),
                           "content": content})
        append_line(self._path, line)  # R-013

    def purge_expired(self) -> int:
        """Drop expired records (rewrite the file); returns how many were removed."""
        if not os.path.exists(self._path):
            return 0
        now = self._clock()
        keep: list[str] = []
        removed = 0
        with open(self._path, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    exp = datetime.fromisoformat(json.loads(line)["expires_at"])
                except (ValueError, KeyError, TypeError):
                    exp = now  # unreadable -> drop (fail toward deletion)
                if exp <= now:
                    removed += 1
                else:
                    keep.append(line if line.endswith("\n") else line + "\n")
        with open(self._path, "w", encoding="utf-8") as f:
            f.writelines(keep)
        return removed
