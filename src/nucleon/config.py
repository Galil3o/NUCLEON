"""Typed configuration for Nucleon. Holds no account identity or secrets."""
from __future__ import annotations

import ipaddress
from typing import Literal, Optional
from urllib.parse import urlsplit

import yaml
from nucleon.accounts import ACCOUNT_ID_RE
from nucleon.addresses import normalize_address
from nucleon.budgets import (
    DEFAULT_MAX_ACTIONS,
    DEFAULT_MAX_BODY_BYTES,
    DEFAULT_MAX_HEADER_BYTES,
    DEFAULT_MAX_LABELS,
    DEFAULT_MAX_MESSAGE_BYTES,
    DEFAULT_MAX_MODEL_INPUT_CHARS,
    DEFAULT_MAX_MODEL_RESPONSE_BYTES,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class MailConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # R-012: stable internal account id. All state is bound to it, never to the
    # login name. Changing it orphans the state; changing host/username under
    # the same id needs ``accept_identity_change`` (an explicit decision).
    id: str
    accept_identity_change: bool = False
    mode: str
    host: str
    port: int
    username: str
    mailbox: str = "INBOX"
    since_days: int = Field(default=7, ge=1)
    limit: int = Field(default=50, ge=1)  # R-026: 0/negative would silently process nothing
    # Optional PEM CA bundle to trust for the IMAP TLS handshake (e.g. Proton
    # Bridge's self-signed cert). Verification stays ON either way. None = system CAs.
    tls_ca_file: Optional[str] = None


    @field_validator("id")
    @classmethod
    def _id_shape(cls, v: str) -> str:
        if not ACCOUNT_ID_RE.fullmatch(v):
            raise ValueError(
                "mail.id must be 1-64 chars of letters, digits, '.', '_' or '-' "
                "and start with a letter or digit"
            )
        return v


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
    # Omitted from the model request by default (None). Claude 4.7+ / Gemini reject
    # temperature < 1 via the OpenAI-compat layer; set this only for a local model.
    temperature: Optional[float] = None

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
    # R-010: let the preflight CREATE missing non-system folders (outbox/labels).
    # Off by default: a missing folder blocks the write run instead.
    create_missing_folders: bool = False


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


class LimitsConfig(BaseModel):
    """Size/count budgets (R-011). Overruns are reported per message."""

    model_config = ConfigDict(extra="forbid")

    max_message_bytes: int = Field(default=DEFAULT_MAX_MESSAGE_BYTES, gt=0)  # RFC822.SIZE; over = rejected
    max_body_bytes: int = Field(default=DEFAULT_MAX_BODY_BYTES, gt=0)  # text part fetch cap; over = truncated
    max_header_bytes: int = Field(default=DEFAULT_MAX_HEADER_BYTES, gt=0)
    max_model_response_bytes: int = Field(default=DEFAULT_MAX_MODEL_RESPONSE_BYTES, gt=0)
    max_model_input_chars: int = Field(default=DEFAULT_MAX_MODEL_INPUT_CHARS, gt=0)  # body sent to model
    max_labels_per_message: int = Field(default=DEFAULT_MAX_LABELS, gt=0)
    max_actions_per_message: int = Field(default=DEFAULT_MAX_ACTIONS, gt=0)


class AnalysisConfig(BaseModel):
    """Error analysis (R-008). Stage 2 = metadata-only records; Stage 3 = content,
    needs ``verbose`` plus a separate explicit per-message confirmation."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    path: str = "nucleon-state/analysis.jsonl"
    verbose: bool = False  # Stage 3 toggle; never sufficient on its own
    verbose_path: str = "nucleon-state/analysis-stage3.jsonl"
    verbose_ttl_seconds: int = Field(default=900, ge=1, le=3600)


class RecipientsConfig(BaseModel):
    """Reply recipients (DECISIONS section 4) and the address boundary (R-009).

    Per account (this block lives in the account's config). ``sender-only`` is
    the default; model proposals are only ever considered in ``model-decided``
    and then ONLY resolve against ``contacts`` (plus opt-in learned addresses).
    """

    model_config = ConfigDict(extra="forbid")

    mode: Literal["sender-only", "sender-and-recipients", "model-decided"] = "sender-only"
    contacts: list[str] = Field(default_factory=list)  # approved addresses of this account
    own_addresses: list[str] = Field(default_factory=list)  # excluded from reply-all
    learned_enabled: bool = False  # history-learned addresses: opt-in, OFF by default
    learned_path: str = "nucleon-state/contacts-learned.jsonl"  # separate store
    banner: bool = True  # opt-out switch for the differing-recipient banner
    banner_language: Literal["de", "en"] = "de"  # the operator's program language

    @field_validator("contacts", "own_addresses")
    @classmethod
    def _addresses(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for raw in v:
            a = normalize_address(raw)
            if a is None:
                raise ValueError("recipients: not a single valid address")
            if a not in out:
                out.append(a)
        return out


class PresenceCheckConfig(BaseModel):
    """Attachment-presence check against the Drive DataMap. OFF by default: the
    live attachment fetch it would drive is disk-sensitive and supervised."""

    enabled: bool = False
    under: str | None = None  # restrict the checked Drive subtree (None = whole map)


class DriveConfig(BaseModel):
    presence_check: PresenceCheckConfig = Field(default_factory=PresenceCheckConfig)


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mail: MailConfig
    classifier: ClassifierConfig
    draft: DraftConfig = Field(default_factory=DraftConfig)
    cleanup: CleanupConfig = Field(default_factory=CleanupConfig)
    journal: JournalConfig = Field(default_factory=JournalConfig)
    labels: LabelsConfig = Field(default_factory=LabelsConfig)
    marker: MarkerConfig = Field(default_factory=MarkerConfig)
    analysis: AnalysisConfig = Field(default_factory=AnalysisConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    recipients: RecipientsConfig = Field(default_factory=RecipientsConfig)
    drive: DriveConfig = Field(default_factory=DriveConfig)
    # R-012: where account id -> identity fingerprint (hashes only) is kept.
    registry_path: str = "nucleon-state/accounts.json"


class ConfigError(ValueError):
    """The config file cannot be parsed (reported with exit status 2)."""


def load_config(path: str) -> Config:
    with open(path, encoding="utf-8") as fh:
        try:
            data = yaml.safe_load(fh)
        except yaml.YAMLError as exc:
            # R-015: a YAML syntax error is a config error, not a traceback.
            # Keep only the parser's short problem text and the position; never
            # the surrounding file content.
            mark = getattr(exc, "problem_mark", None)
            problem = str(getattr(exc, "problem", None) or "invalid YAML")[:100]
            where = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
            raise ConfigError(f"YAML syntax error{where}: {problem}") from None
    return Config.model_validate(data)
