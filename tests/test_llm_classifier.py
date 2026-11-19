import json

import pytest

from nucleon.models import Classification, MessageMeta, MessageRef
from nucleon.quarantine import Classifier, ClassifierOutputError
from nucleon.quarantine.llm import LLMClassifier

GOOD = {
    "category": "invoice",
    "labels": ["finance"],
    "urgency": "normal",
    "needs_reply": False,
    "draft_subject": None,
    "draft_body": None,
}


def _meta() -> MessageMeta:
    return MessageMeta(
        ref=MessageRef(account_hash="h", mailbox="INBOX", uidvalidity=1, uid=1, raw_hash="r"),
        subject="Invoice 42",
        from_addr="a@example.com",
        to_addrs=["me@example.com"],
    )


def _resp(content):
    return {"choices": [{"message": {"content": content}}]}


def _clf(stub):
    return LLMClassifier("http://localhost:11434/", "m1", http_post=stub)


def test_valid_response_parsed_to_classification():
    calls = []

    def stub(url, payload, headers):
        calls.append((url, payload))
        return _resp(json.dumps(GOOD))

    result = _clf(stub).classify(_meta(), "please pay BODYTEXT")
    assert isinstance(result, Classification)
    assert result.category == "invoice"
    url, payload = calls[0]
    assert url == "http://localhost:11434/v1/chat/completions"
    assert payload["model"] == "m1"
    assert payload["temperature"] == 0 and payload["stream"] is False
    assert "BODYTEXT" in json.dumps(payload)
    assert isinstance(_clf(stub), Classifier)


def test_malformed_response_raises_output_error():
    with pytest.raises(ClassifierOutputError):
        _clf(lambda u, p, h: _resp("not json at all")).classify(_meta(), "b")


def test_missing_choices_raises_output_error():
    with pytest.raises(ClassifierOutputError):
        _clf(lambda u, p, h: {}).classify(_meta(), "b")


def test_fenced_json_is_parsed():
    fenced = "```json\n" + json.dumps(GOOD) + "\n```"
    assert _clf(lambda u, p, h: _resp(fenced)).classify(_meta(), "b").category == "invoice"


# --- provider-auth seam (multi-provider: Claude / Gemini / local) ---


def test_api_key_sent_as_bearer_authorization_header():
    seen = {}

    def stub(url, payload, headers):
        seen.update(headers)
        return _resp(json.dumps(GOOD))

    clf = LLMClassifier("https://api.anthropic.com", "claude-x", http_post=stub, api_key="SECRET123")
    clf.classify(_meta(), "b")
    assert seen.get("Authorization") == "Bearer SECRET123"


def test_no_api_key_sends_no_authorization_header():
    seen = {}

    def stub(url, payload, headers):
        seen.update(headers)
        return _resp(json.dumps(GOOD))

    LLMClassifier("http://localhost:11434/", "m1", http_post=stub).classify(_meta(), "b")
    assert "Authorization" not in seen


def test_full_chat_completions_url_used_verbatim():
    calls = []

    def stub(url, payload, headers):
        calls.append(url)
        return _resp(json.dumps(GOOD))

    # Gemini's OpenAI-compatible path differs from /v1/chat/completions
    ep = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    LLMClassifier(ep, "gemini-x", http_post=stub).classify(_meta(), "b")
    assert calls[0] == ep
