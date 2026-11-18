"""Typed configuration for Nucleon. Holds no account identity or secrets."""
from __future__ import annotations

from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


class MailConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str
    host: str
    port: int
    username: str
    mailbox: str = "INBOX"
    since_days: int = 7
    limit: int = 50


class ClassifierConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoint: str
    model: str


class DraftConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: Literal["drafts", "outbox"] = "outbox"
    label: str = "nucleon/proposed"


class CleanupConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    auto_trash_enabled: bool = False
    auto_trash_categories: list[str] = Field(default_factory=list)


class JournalConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    backend: Literal["file"] = "file"
    path: str | None = None


class MarkerConfig(BaseModel):
    """Always-on processing marker (R-003). No ``enabled`` switch by design."""

    model_config = ConfigDict(extra="forbid")

    backend: Literal["file"] = "file"
    path: str = "nucleon-state/processed.jsonl"


class LabelsConfig(BaseModel):
    """R-001: where model-proposed labels may point.

    A model label is only honoured inside the reserved automation ``namespace``
    or when it exactly matches an operator-approved, provider-resolved target
    in ``allowed``. System/special folders are never valid targets.
    """

    model_config = ConfigDict(extra="forbid")

    namespace: str = "nucleon/"
    allowed: list[str] = Field(default_factory=list)

    @field_validator("namespace")
    @classmethod
    def _namespace_shape(cls, v: str) -> str:
        if len(v) < 2 or not v.endswith("/") or v.startswith("/") or ".." in v:
            raise ValueError("labels.namespace must look like 'name/' (non-empty, trailing slash)")
        return v


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mail: MailConfig
    classifier: ClassifierConfig
    draft: DraftConfig = Field(default_factory=DraftConfig)
    cleanup: CleanupConfig = Field(default_factory=CleanupConfig)
    journal: JournalConfig = Field(default_factory=JournalConfig)
    labels: LabelsConfig = Field(default_factory=LabelsConfig)
    marker: MarkerConfig = Field(default_factory=MarkerConfig)


def load_config(path: str) -> Config:
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return Config.model_validate(data)
