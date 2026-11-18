"""R-012: stable internal account ID; state is bound to it, not to the login."""
import hashlib

import pytest
from pydantic import ValidationError

from nucleon import cli
from nucleon.accounts import (
    AccountIdentityChanged,
    AccountRegistry,
    DuplicateAccountIdError,
    account_hash,
    account_hash_for,
    validate_unique_account_ids,
)
from nucleon.config import Config
from nucleon.engine import ReconcileReport, reconcile, run_once
from nucleon.journal import FileJournalStore, StoredJournal
from nucleon.mailstore.fake import FakeMailStore
from nucleon.marker import FileMarkerStore, ProcessingMarker
from nucleon.models import Classification, MessageMeta, MessageRef
from nucleon.quarantine.fake import FakeClassifier


def _cfg(acct="acct-1", host="h", user="u", **mail) -> Config:
    return Config.model_validate({
        "mail": {"mode": "imap", "host": host, "port": 993, "username": user, "id": acct, **mail},
        "classifier": {"endpoint": "http://127.0.0.1:1", "model": "m"},
    })


def test_id_is_required_and_well_formed():
    with pytest.raises(ValidationError):
        Config.model_validate({
            "mail": {"mode": "imap", "host": "h", "port": 1, "username": "u"},
            "classifier": {"endpoint": "http://127.0.0.1:1", "model": "m"}})
    for bad in ("", "A B", "x/y", "../x", "a" * 65, "Ünï"):
        with pytest.raises(ValidationError):
            _cfg(acct=bad)


def test_same_login_on_different_hosts_does_not_collide():
    a = _cfg(acct="work", host="imap.one.example", user="info")
    b = _cfg(acct="private", host="imap.two.example", user="info")
    assert account_hash_for(a) != account_hash_for(b)
    legacy = hashlib.sha256(b"info").hexdigest()  # the v0.1 login-only hash
    assert account_hash_for(a) != legacy and account_hash_for(b) != legacy
    ra = MessageRef.from_source(account_hash_for(a), "INBOX", 1, 1, "<m@x>", b"raw")
    rb = MessageRef.from_source(account_hash_for(b), "INBOX", 1, 1, "<m@x>", b"raw")
    assert ra.processing_key() != rb.processing_key()


def test_account_hash_is_a_pure_function_of_the_id_not_the_login():
    a = _cfg(acct="same", host="h1", user="u1")
    b = _cfg(acct="same", host="h2", user="u2")
    assert account_hash_for(a) == account_hash_for(b) == account_hash("same")


def test_duplicate_ids_rejected():
    validate_unique_account_ids(["a", "b"])
    with pytest.raises(DuplicateAccountIdError):
        validate_unique_account_ids(["a", "b", "a"])
    with pytest.raises(DuplicateAccountIdError):  # IDs are compared case-insensitively
        validate_unique_account_ids(["Work", "work"])


def test_registry_records_first_use_and_accepts_unchanged(tmp_path):
    reg = AccountRegistry(str(tmp_path / "accounts.json"))
    assert reg.check(_cfg()) == "new"
    assert AccountRegistry(str(tmp_path / "accounts.json")).check(_cfg()) == "unchanged"


@pytest.mark.parametrize("change", [{"host": "other"}, {"user": "someone-else"}])
def test_login_or_server_change_forces_explicit_decision(tmp_path, change):
    path = str(tmp_path / "accounts.json")
    AccountRegistry(path).check(_cfg())
    with pytest.raises(AccountIdentityChanged) as ei:
        AccountRegistry(path).check(_cfg(**change))
    assert "acct-1" in str(ei.value) and "accept_identity_change" in str(ei.value)
    # still refused on the next run: the decision is not implied by trying twice
    with pytest.raises(AccountIdentityChanged):
        AccountRegistry(path).check(_cfg(**change))


def test_explicit_acceptance_keeps_id_and_state_and_updates_record(tmp_path):
    path = str(tmp_path / "accounts.json")
    AccountRegistry(path).check(_cfg())
    changed = _cfg(host="other", accept_identity_change=True)
    assert AccountRegistry(path).check(changed) == "changed-accepted"
    assert account_hash_for(changed) == account_hash_for(_cfg())  # state is reused
    assert AccountRegistry(path).check(_cfg(host="other")) == "unchanged"


def test_registry_stores_no_plaintext_identity(tmp_path):
    path = tmp_path / "accounts.json"
    AccountRegistry(str(path)).check(_cfg(host="imap.secret.example", user="alice@example.com"))
    text = path.read_text()
    assert "secret" not in text and "alice" not in text and "acct-1" in text


def test_cli_refuses_changed_identity_without_decision(tmp_path, capsys):
    def write(host, extra=""):
        p = tmp_path / "config.yaml"
        p.write_text(
            f"mail: {{mode: imap, host: {host}, port: 993, username: u, id: acct-1{extra}}}\n"
            'classifier: {endpoint: "http://127.0.0.1:1", model: m}\n'
            f"marker: {{path: {tmp_path / 'm.jsonl'}}}\n"
            f"registry_path: {tmp_path / 'accounts.json'}\n", encoding="utf-8")
        return str(p)

    from nucleon.journal import NullJournal
    deps = lambda cfg: (FakeMailStore(), FakeClassifier(None), NullJournal())  # noqa: E731
    assert cli.main(["run", "--config", write("h1")], deps=deps) == 0
    assert cli.main(["run", "--config", write("h2")], deps=deps) == 2
    assert "accept_identity_change" in capsys.readouterr().err
    assert cli.main(["run", "--config", write("h2", ", accept_identity_change: true")],
                    deps=deps) == 0


def _meta(acct, uid=1, mid="<in@x>") -> MessageMeta:
    ref = MessageRef.from_source(account_hash(acct), "INBOX", 1, uid, mid, b"raw")
    return MessageMeta(ref=ref, subject="S", from_addr="a@b.c", to_addrs=[], message_id=mid)


def test_marker_state_is_isolated_per_account_id(tmp_path):
    """Two accounts, same login, same marker file: neither skips the other's mail."""
    marker = ProcessingMarker(FileMarkerStore(str(tmp_path / "m.jsonl")))
    j = StoredJournal(FileJournalStore(str(tmp_path / "j.jsonl")))
    clf = FakeClassifier(Classification(category="c", labels=[], urgency="normal",
                                        needs_reply=False))
    for acct in ("work", "private"):
        store = FakeMailStore()
        store.seed_inbox(_meta(acct), "body")
        cfg = _cfg(acct=acct, host=acct + ".example", user="info")
        assert run_once(store, clf, j, cfg, marker).processed == 1


def test_reconcile_only_sees_this_accounts_drafts(tmp_path):
    marker = ProcessingMarker(FileMarkerStore(str(tmp_path / "m.jsonl")))
    j = StoredJournal(FileJournalStore(str(tmp_path / "j.jsonl")))
    clf = FakeClassifier(Classification(category="c", labels=[], urgency="normal",
                                        needs_reply=True, draft_body="b"))
    other = FakeMailStore()
    other.seed_inbox(_meta("other"), "body")
    run_once(other, clf, j, _cfg(acct="other"), marker)
    sent = _meta("other", uid=9, mid="<s@x>")
    sent = sent.model_copy(update={"in_reply_to": "<in@x>"})
    store = FakeMailStore()
    store.seed_sent(sent)
    # this account ("acct-1") must not touch the other account's open draft
    assert reconcile(store, j, _cfg(acct="acct-1")) == ReconcileReport(0, 0)
    assert reconcile(store, j, _cfg(acct="other")) == ReconcileReport(sent=0, observed=1)
