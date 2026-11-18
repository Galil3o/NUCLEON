from pathlib import Path

import pytest
from pydantic import ValidationError

from nucleon.config import Config, load_config

EXAMPLE = Path(__file__).resolve().parent.parent / "config.example.yaml"


def test_load_example_config_is_valid():
    cfg = load_config(str(EXAMPLE))
    assert cfg.journal.enabled is False
    assert cfg.cleanup.auto_trash_enabled is False
    assert cfg.draft.label == "nucleon/proposed"
    assert cfg.draft.target == "outbox"


def test_missing_required_field_raises():
    with pytest.raises(ValidationError):
        Config.model_validate({"mail": {"host": "127.0.0.1"}})


def test_unknown_key_raises():
    base = {
        "mail": {"mode": "imap", "host": "h", "port": 1, "username": "u", "id": "acct-1"},
        "classifier": {"endpoint": "http://127.0.0.1:1", "model": "m"},
    }
    Config.model_validate(base)
    with pytest.raises(ValidationError):
        Config.model_validate({**base, "bogus": 1})
    with pytest.raises(ValidationError):
        Config.model_validate({**base, "mail": {**base["mail"], "hots": "x"}})


# --- R-007: the model endpoint is a privacy boundary -------------------------
def _with_endpoint(endpoint, **extra):
    return Config.model_validate({
        "mail": {"mode": "imap", "host": "h", "port": 1, "username": "u", "id": "acct-1"},
        "classifier": {"endpoint": endpoint, "model": "m", **extra},
    })


@pytest.mark.parametrize("ep", [
    "http://127.0.0.1:11434", "http://localhost:11434", "http://[::1]:11434",
    "https://localhost:8443", "http://127.1.2.3",
])
def test_r007_local_endpoint_needs_no_opt_in(ep):
    assert _with_endpoint(ep).classifier.allow_remote is False


@pytest.mark.parametrize("ep", ["http://models.example.com", "http://10.0.0.5:11434",
                                "http://127.0.0.1.evil.example.com"])
def test_r007_remote_plaintext_http_rejected_even_with_opt_in(ep):
    with pytest.raises(ValidationError, match="TLS|https"):
        _with_endpoint(ep, allow_remote=True)


@pytest.mark.parametrize("ep", ["https://models.example.com/api", "https://10.0.0.5:8443",
                                "https://0.0.0.0:1"])
def test_r007_remote_https_requires_explicit_opt_in(ep):
    with pytest.raises(ValidationError, match="allow_remote"):
        _with_endpoint(ep)
    assert _with_endpoint(ep, allow_remote=True).classifier.endpoint == ep


@pytest.mark.parametrize("ep", [
    "ftp://127.0.0.1", "127.0.0.1:11434", "http://", "http://user:pw@127.0.0.1",
    "http://127.0.0.1@evil.example.com", "e",
])
def test_r007_malformed_or_credentialed_endpoint_rejected(ep):
    with pytest.raises(ValidationError):
        _with_endpoint(ep, allow_remote=True)
