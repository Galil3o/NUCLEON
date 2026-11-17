import json

import pytest

from nucleon.models import Classification, MessageMeta, MessageRef
from nucleon.quarantine import Classifier, ClassifierOutputError, parse_classification
from nucleon.quarantine.fake import FakeClassifier

VALID = {
    "category": "billing",
    "labels": ["invoice"],
    "urgency": "high",
    "needs_reply": True,
    "draft_subject": "Re: Invoice",
    "draft_body": "Thanks.",
}


def test_parse_valid_output():
    c = parse_classification(json.dumps(VALID))
    assert c == Classification(**VALID)


def test_parse_rejects_non_json():
    with pytest.raises(ClassifierOutputError):
        parse_classification("not json at all")


def test_parse_rejects_missing_field():
    data = {k: v for k, v in VALID.items() if k != "category"}
    with pytest.raises(ClassifierOutputError):
        parse_classification(json.dumps(data))


@pytest.mark.parametrize("raw", ["[]", "42", '"str"', "null"])
def test_parse_rejects_non_object_json(raw):
    with pytest.raises(ClassifierOutputError):
        parse_classification(raw)


def test_parse_rejects_extra_field():
    with pytest.raises(ClassifierOutputError):
        parse_classification(json.dumps({**VALID, "forward_to": "evil@example.com"}))


def test_parse_rejects_wrong_type():
    with pytest.raises(ClassifierOutputError):
        parse_classification(json.dumps({**VALID, "needs_reply": "yes"}))


def test_fake_classifier_returns_result():
    result = Classification(**VALID)
    fake = FakeClassifier(result)
    meta = MessageMeta(
        ref=MessageRef.from_source("a", "INBOX", 1, 1, "<m@x>", b"raw"),
        subject="s",
        from_addr="a@b.c",
        to_addrs=["d@e.f"],
    )
    assert isinstance(fake, Classifier)
    assert fake.classify(meta, "body") == result


def test_error_message_does_not_echo_untrusted_content():
    sentinel = "SENTINEL_UNTRUSTED_9f3"
    bad = [
        json.dumps({"category": "x", "labels": [], "urgency": "normal", "needs_reply": sentinel}),
        json.dumps({sentinel: 1}),
        "not json " + sentinel,
    ]
    for raw in bad:
        with pytest.raises(ClassifierOutputError) as ei:
            parse_classification(raw)
        assert sentinel not in str(ei.value)


def test_deeply_nested_json_maps_to_classifier_error():
    with pytest.raises(ClassifierOutputError):
        parse_classification("[" * 100000)
