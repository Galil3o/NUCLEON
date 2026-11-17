"""Typed configuration for Nucleon. Holds no account identity or secrets."""
from __future__ import annotations

from typing import Literal

import yaml
from pydantic import BaseModel, Field


class MailConfig(BaseModel):
    mode: str
    host: str
    port: int
    username: str
    mailbox: str = "INBOX"
    since_days: int = 7
    limit: int = 50


class ClassifierConfig(BaseModel):
    endpoint: str
    model: str


class DraftConfig(BaseModel):
    target: Literal["drafts", "outbox"] = "outbox"
    label: str = "nucleon/proposed"


class CleanupConfig(BaseModel):
    auto_trash_enabled: bool = False
    auto_trash_categories: list[str] = Field(default_factory=list)


class JournalConfig(BaseModel):
    enabled: bool = False
    backend: Literal["file"] = "file"
    path: str | None = None


class Config(BaseModel):
    mail: MailConfig
    classifier: ClassifierConfig
    draft: DraftConfig = Field(default_factory=DraftConfig)
    cleanup: CleanupConfig = Field(default_factory=CleanupConfig)
    journal: JournalConfig = Field(default_factory=JournalConfig)


def load_config(path: str) -> Config:
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return Config.model_validate(data)
