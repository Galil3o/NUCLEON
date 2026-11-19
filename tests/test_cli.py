import pytest

from nucleon import cli
from nucleon.config import Config
from nucleon.journal import NullJournal
from nucleon.mailstore.fake import FakeMailStore
from nucleon.models import Classification, MessageMeta, MessageRef
from nucleon.quarantine.fake import FakeClassifier

CONFIG = """\
mail: {mode: imap, host: h, port: 993, username: u, id: acct-1}
classifier: {endpoint: "http://127.0.0.1:1", model: m}
"""


def _config(tmp_path) -> str:
    p = tmp_path / "config.yaml"
    p.write_text(CONFIG + f"registry_path: {tmp_path / 'acc.json'}\n" + f"marker: {{path: {tmp_path / 'marker.jsonl'}}}\n", encoding="utf-8")
    return str(p)


def _seeded_store() -> FakeMailStore:
    store = FakeMailStore()
    ref = MessageRef.from_source("a", "INBOX", 1, 1, "<in@x>", b"raw1")
    store.seed_inbox(
        MessageMeta(ref=ref, subject="Hi", from_addr="a@b.c", to_addrs=["d@e.f"],
                    message_id="<in@x>"),
        "body",
    )
    return store


def test_run_subcommand_invokes_engine(tmp_path, capsys):
    store = _seeded_store()
    clf = FakeClassifier(
        Classification(category="personal", labels=["nucleon/l1"], urgency="normal",
                       needs_reply=True, draft_body="Thanks")
    )
    rc = cli.main(["run", "--config", _config(tmp_path)],
                  deps=lambda cfg: (store, clf, NullJournal()))
    assert rc == 0
    assert len(store.appended_drafts) == 1
    out = capsys.readouterr().out
    assert "processed=1" in out and "errors=0" in out


def test_reconcile_subcommand_invokes_reconcile(tmp_path, capsys):
    rc = cli.main(["reconcile", "--config", _config(tmp_path)],
                  deps=lambda cfg: (FakeMailStore(), FakeClassifier(None), NullJournal()))
    assert rc == 0
    assert capsys.readouterr().out.strip() == "reconciled=0 observed=0"


def test_help_exits_zero():
    calls = []

    def boom(cfg):
        calls.append(cfg)
        raise AssertionError("deps must not be called")

    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"], deps=boom)
    assert exc.value.code == 0
    assert calls == []


def test_run_returns_one_on_deps_error(tmp_path, capsys):
    def raising(cfg):
        raise RuntimeError("no server")

    rc = cli.main(["run", "--config", _config(tmp_path)], deps=raising)
    assert rc == 1
    err = capsys.readouterr().err
    assert "no server" in err and "Traceback" not in err


def test_missing_config_returns_error(tmp_path, capsys):
    rc = cli.main(["run", "--config", str(tmp_path / "nope.yaml")],
                  deps=lambda cfg: pytest.fail("deps must not be called"))
    assert rc == 2
    err = capsys.readouterr().err
    assert "nope.yaml" in err and "Traceback" not in err


def test_run_twice_with_null_journal_creates_no_duplicate_draft(tmp_path, capsys):
    store = _seeded_store()
    clf = FakeClassifier(
        Classification(category="personal", labels=["nucleon/l1"], urgency="normal",
                       needs_reply=True, draft_body="Thanks")
    )
    cfgpath = _config(tmp_path)
    for _ in range(2):
        assert cli.main(["run", "--config", cfgpath],
                        deps=lambda cfg: (store, clf, NullJournal())) == 0
    assert len(store.appended_drafts) == 1
    assert "skipped=1" in capsys.readouterr().out


def test_r008_cli_wires_stage2_only_when_analysis_enabled(tmp_path):
    class Boom:
        def classify(self, meta, body):
            raise ValueError("Subject: secret alice@example.com")

    p = tmp_path / "config.yaml"
    apath = tmp_path / "analysis.jsonl"
    p.write_text(CONFIG + f"registry_path: {tmp_path / 'acc.json'}\n"
                 f"marker: {{path: {tmp_path / 'm.jsonl'}}}\n"
                 f"analysis: {{enabled: true, path: {apath}}}\n", encoding="utf-8")
    rc = cli.main(["run", "--config", str(p)],
                  deps=lambda cfg: (_seeded_store(), Boom(), NullJournal()))
    assert rc == cli.EXIT_RUN_FAILURES  # R-014: the one message errored
    text = apath.read_text()
    assert '"error_code":"value_error"' in text and "secret" not in text and "alice" not in text


# -- R-014: exit status reflects failed runs ----------------------------------

def _ok_clf():
    return FakeClassifier(Classification(category="personal", labels=[], urgency="normal",
                                         needs_reply=False))


def test_r014_exit_codes_are_documented_constants():
    assert (cli.EXIT_OK, cli.EXIT_ERROR, cli.EXIT_CONFIG, cli.EXIT_RUN_FAILURES) == (0, 1, 2, 3)


def test_r014_zero_processed_one_error_is_not_exit_zero(tmp_path, capsys):
    class Boom:
        def classify(self, meta, body):
            raise ValueError("x")

    rc = cli.main(["run", "--config", _config(tmp_path)],
                  deps=lambda cfg: (_seeded_store(), Boom(), NullJournal()))
    assert rc == 3
    assert "processed=0" in capsys.readouterr().out


def test_r014_partial_failure_still_reports_successes(tmp_path, capsys):
    store = _seeded_store()
    ref2 = MessageRef.from_source("a", "INBOX", 1, 2, "<in2@x>", b"raw2")
    store.seed_inbox(MessageMeta(ref=ref2, subject="Hi2", from_addr="a@b.c",
                                 to_addrs=["d@e.f"], message_id="<in2@x>"), "body2")

    class Flaky:
        n = 0

        def classify(self, meta, body):
            Flaky.n += 1
            if Flaky.n == 1:
                raise ValueError("x")
            return Classification(category="personal", labels=[], urgency="normal",
                                  needs_reply=False)

    rc = cli.main(["run", "--config", _config(tmp_path)],
                  deps=lambda cfg: (store, Flaky(), NullJournal()))
    out = capsys.readouterr().out
    assert rc == 3 and "processed=1" in out and "errors=1" in out


def test_r014_over_budget_is_a_failed_run(tmp_path, capsys):
    from nucleon.budgets import BudgetExceeded

    store = _seeded_store()

    def too_big(ref):
        raise BudgetExceeded("message_size", 1, 2)

    store.fetch_body = too_big  # type: ignore[method-assign]
    rc = cli.main(["run", "--config", _config(tmp_path)],
                  deps=lambda cfg: (store, _ok_clf(), NullJournal()))
    assert rc == 3 and "over_budget=1" in capsys.readouterr().out


def test_r014_clean_run_and_vanished_only_are_exit_zero(tmp_path):
    assert cli.main(["run", "--config", _config(tmp_path)],
                    deps=lambda cfg: (_seeded_store(), _ok_clf(), NullJournal())) == 0


# -- R-015: YAML parser errors are config errors (exit 2) ---------------------

@pytest.mark.parametrize("bad", [
    "mail: {mode: imap\nhost: [unclosed\n",
    "a: b: c\n",
    "\tkey: tab-indented\n",
    "key: 'SECRET-VALUE-XYZ\n",
])
def test_r015_broken_yaml_is_config_error_exit_2(tmp_path, capsys, bad):
    p = tmp_path / "config.yaml"
    p.write_text(bad, encoding="utf-8")
    rc = cli.main(["run", "--config", str(p)],
                  deps=lambda cfg: pytest.fail("deps must not be called"))
    assert rc == 2
    err = capsys.readouterr().err
    assert "Traceback" not in err and "line" in err and "column" in err
    assert "SECRET-VALUE-XYZ" not in err and len(err) < 400  # no raw dump


def test_tls_context_uses_system_cas_when_no_ca_file(monkeypatch):
    import ssl

    from nucleon import cli
    calls = []
    real = ssl.create_default_context
    monkeypatch.setattr(cli.ssl, "create_default_context",
                        lambda **kw: calls.append(kw) or real(**kw))
    ctx = cli.make_tls_context(_tls_cfg(None))
    assert calls == [{}]
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname


def test_tls_context_passes_configured_ca_file_and_keeps_verification(monkeypatch):
    import ssl

    from nucleon import cli
    calls = []
    sentinel = ssl.create_default_context()
    monkeypatch.setattr(cli.ssl, "create_default_context",
                        lambda **kw: calls.append(kw) or sentinel)
    ctx = cli.make_tls_context(_tls_cfg("/path/to/bridge-ca.pem"))
    assert calls == [{"cafile": "/path/to/bridge-ca.pem"}]
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname


def test_tls_context_with_missing_ca_file_fails_closed(tmp_path):
    from nucleon import cli
    with pytest.raises(OSError):
        cli.make_tls_context(_tls_cfg(str(tmp_path / "nope.pem")))


def _tls_cfg(ca):
    mail = {"mode": "imap", "host": "h", "port": 2143, "username": "u", "id": "acct-1"}
    if ca is not None:
        mail["tls_ca_file"] = ca
    return Config.model_validate({
        "mail": mail, "classifier": {"endpoint": "http://127.0.0.1:1", "model": "m"}})
