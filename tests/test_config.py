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
        "mail": {"mode": "imap", "host": "h", "port": 1, "username": "u"},
        "classifier": {"endpoint": "e", "model": "m"},
    }
    Config.model_validate(base)
    with pytest.raises(ValidationError):
        Config.model_validate({**base, "bogus": 1})
    with pytest.raises(ValidationError):
        Config.model_validate({**base, "mail": {**base["mail"], "hots": "x"}})
