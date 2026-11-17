"""Quarantine boundary: untrusted model text becomes a Classification only via strict validation."""
from __future__ import annotations

import json
from typing import Protocol, runtime_checkable

from pydantic import ValidationError

from nucleon.models import Classification, MessageMeta


class ClassifierOutputError(Exception):
    """Raised when classifier output fails strict validation."""


@runtime_checkable
class Classifier(Protocol):
    def classify(self, meta: MessageMeta, body_text: str) -> Classification: ...


def parse_classification(raw: str) -> Classification:
    try:
        data = json.loads(raw)
    except (ValueError, TypeError, RecursionError) as e:
        raise ClassifierOutputError("classifier output is not valid JSON") from e
    if not isinstance(data, dict):
        raise ClassifierOutputError("output JSON is not an object")
    extra = set(data) - set(Classification.model_fields)
    if extra:
        raise ClassifierOutputError("classifier output failed validation")
    try:
        return Classification.model_validate(data, strict=True)
    except ValidationError as e:
        raise ClassifierOutputError("classifier output failed validation") from e
