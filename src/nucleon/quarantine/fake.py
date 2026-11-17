"""Deterministic classifier for tests."""
from __future__ import annotations

from nucleon.models import Classification, MessageMeta


class FakeClassifier:
    def __init__(self, result: Classification) -> None:
        self._result = result

    def classify(self, meta: MessageMeta, body_text: str) -> Classification:
        return self._result
