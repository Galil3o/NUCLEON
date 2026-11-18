"""Typed configuration for Nucleon. Holds no account identity or secrets."""
from __future__ import annotations

import ipaddress
from typing import Literal
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class MailConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str
    host: str
    port: int
    username: str
    mailbox: str = "INBOX"
    since_days: int = 7
    limit: int = 50


def _is_loopback_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False  # any other name (even "127.0.0.1.evil.example") is remote


class ClassifierConfig(BaseModel):
    """The model endpoint is a PRIVACY BOUNDARY (R-007).

    The classifier sends message text, subject and addresses to this URL.
    Loopback endpoints (127.0.0.0/8, ::1, ``localhost``) are allowed by default.
    Any other host is "remote" and needs BOTH an explicit ``allow_remote: true``
    AND ``https`` (TLS); plaintext http to a remote host is always rejected.
    """

    model_config = ConfigDict(extra="forbid")

    endpoint: str
    model: str
    allow_remote: bool = False

    @model_validator(mode="after")
    def _endpoint_is_privacy_safe(self) -> "ClassifierConfig":
        try:
            parts = urlsplit(self.endpoint)
            host = parts.hostname
            parts.port  # noqa: B018 (raises ValueError on a malformed port)
        except ValueError as e:
            raise ValueError("classifier.endpoint is not a valid URL") from e
        if parts.scheme not in ("http", "https") or not host:
            raise ValueError("classifier.endpoint must be an http(s) URL with a host")
        if parts.username is not None or parts.password is not None or "@" in parts.netloc:
            raise ValueError("classifier.endpoint must not embed credentials")
        if _is_loopback_host(host):
            return self
        if parts.scheme != "https":
            raise ValueError(
                "classifier.endpoint: a remote model endpoint requires TLS (https); "
                "plaintext http to a remote host is rejected"
            )
        if not self.allow_remote:
            raise ValueError(
                "classifier.endpoint is remote: mail content would leave this machine; "
                "set classifier.allow_remote: true to opt in explicitly"
            )
        return self


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
