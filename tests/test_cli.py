import pytest

from nucleon import cli
from nucleon.journal import NullJournal
from nucleon.mailstore.fake import FakeMailStore
from nucleon.models import Classification, MessageMeta, MessageRef
from nucleon.quarantine.fake import FakeClassifier

CONFIG = """\
mail: {mode: imap, host: h, port: 993, username: u}
classifier: {endpoint: "http://127.0.0.1:1", model: m}
"""


def _config(tmp_path) -> str:
    p = tmp_path / "config.yaml"
    p.write_text(CONFIG, encoding="utf-8")
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
        Classification(category="personal", labels=["l1"], urgency="normal",
                       needs_reply=True, draft_body="Thanks")
    )
    rc = cli.main(["run", "--config", _config(tmp_path)],
                  deps=lambda cfg: (store, clf, NullJournal()))
    assert rc == 0
    assert len(store.appended_drafts) == 1
    assert "processed=1" in capsys.readouterr().out


def test_reconcile_subcommand_invokes_reconcile(tmp_path, capsys):
    rc = cli.main(["reconcile", "--config", _config(tmp_path)],
                  deps=lambda cfg: (FakeMailStore(), FakeClassifier(None), NullJournal()))
    assert rc == 0
    assert "0" in capsys.readouterr().out


def test_help_exits_zero(monkeypatch):
    def boom(cfg):
        raise AssertionError("build_default_deps must not be called")

    monkeypatch.setattr(cli, "build_default_deps", boom)
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0


def test_missing_config_returns_error(tmp_path, capsys):
    rc = cli.main(["run", "--config", str(tmp_path / "nope.yaml")],
                  deps=lambda cfg: pytest.fail("deps must not be called"))
    assert rc == 2
    err = capsys.readouterr().err
    assert "nope.yaml" in err and "Traceback" not in err
