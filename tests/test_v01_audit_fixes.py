"""Fixes derived from the independent v0.1 architecture/security audit."""
import re

import pytest
from pydantic import ValidationError

from nucleon.config import Config
from nucleon.gate import _clean_subject, decide
from nucleon.models import Classification, MessageMeta, MessageRef, StageDraft
from nucleon.mailstore.imap import ImapMailStore
from nucleon.quarantine.llm import LLMClassifier, _NoRedirectHandler


def _meta():
    ref = MessageRef(account_hash="a", mailbox="INBOX", uidvalidity=1, uid=1, raw_hash="h")
    return MessageMeta(ref=ref, subject="Hallo", from_addr="x@example.com",
                       to_addrs=["me@example.com"], date=None)


def _cfg():
    return Config.model_validate({
        "mail": {"mode": "imap", "host": "h", "port": 1, "username": "u", "id": "acct-1"},
        "classifier": {"endpoint": "http://127.0.0.1:1", "model": "m"},
    })


# --- draft subject sanitizing (header-injection / EmailMessage crash) ---
def test_clean_subject_strips_control_chars():
    assert _clean_subject("Re: Test\r\nBcc: evil@x") == "Re: TestBcc: evil@x"
    assert _clean_subject("\r\n\r\n") is None
    assert _clean_subject(None) is None
    assert _clean_subject("ok") == "ok"


def test_decide_staged_subject_has_no_control_chars():
    cls = Classification(category="c", labels=[], urgency="normal", needs_reply=True,
                         draft_subject="Hi\r\nX-Inject: 1", draft_body="body")
    drafts = [a for a in decide(cls, _meta(), _cfg()) if isinstance(a, StageDraft)]
    assert drafts and "\r" not in drafts[0].subject and "\n" not in drafts[0].subject


# --- random per-request delimiter (delimiter-injection) ---
def test_user_message_uses_unpredictable_per_request_markers():
    clf = LLMClassifier(endpoint="http://127.0.0.1:1", model="m")
    m1 = clf._user_message(_meta(), "body one")
    m2 = clf._user_message(_meta(), "body two")
    assert "<<<UNTRUSTED_EMAIL\n" not in m1          # old static open is gone
    assert "UNTRUSTED_EMAIL>>>" not in m1            # old static close is gone
    assert re.search(r"<<<UNTRUSTED_EMAIL_[0-9a-f]{16}\n", m1)
    assert "body one" in m1
    assert m1 != m2                                   # fresh random token per request


# --- no-redirect opener (R-007: don't leak the Authorization header on a redirect) ---
def test_model_client_refuses_redirects():
    assert _NoRedirectHandler().redirect_request(
        None, None, 302, "Found", {}, "http://evil.example.com") is None


# --- port bounds ---
@pytest.mark.parametrize("bad", [0, 70000, -5])
def test_port_out_of_range_rejected(bad):
    with pytest.raises(ValidationError):
        Config.model_validate({
            "mail": {"mode": "imap", "host": "h", "port": bad, "username": "u", "id": "acct-1"},
            "classifier": {"endpoint": "http://127.0.0.1:1", "model": "m"},
        })


# --- IMAP COPY/MOVE mailbox quoting (labels with spaces broke the command) ---
def test_imap_mailbox_arg_quotes_only_when_needed():
    q = ImapMailStore._mailbox_arg
    assert q("nucleon/proposed") == "nucleon/proposed"   # atom-safe -> as-is
    assert q("Labels/Work") == "Labels/Work"
    assert q("Work Urgent") == '"Work Urgent"'            # space -> quoted string
