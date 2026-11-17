"""Command-line entrypoint: ``nucleon run`` and ``nucleon reconcile``."""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
from typing import Any, Callable

from pydantic import ValidationError

from nucleon.config import Config, load_config
from nucleon.engine import reconcile, run_once
from nucleon.journal import build_journal

Deps = Callable[[Config], tuple[Any, Any, Any]]


def build_default_deps(cfg: Config) -> tuple[Any, Any, Any]:
    """Wire the real adapters. Needs a live IMAP server; the password comes
    only from the NUCLEON_IMAP_PASSWORD environment variable."""
    import imaplib

    from nucleon.mailstore.imap import ImapMailStore
    from nucleon.quarantine.llm import LLMClassifier

    password = os.environ.get("NUCLEON_IMAP_PASSWORD")
    if not password:
        raise RuntimeError("NUCLEON_IMAP_PASSWORD is not set")
    conn = imaplib.IMAP4(cfg.mail.host, cfg.mail.port)
    conn.login(cfg.mail.username, password)
    account_hash = hashlib.sha256(cfg.mail.username.encode()).hexdigest()
    store = ImapMailStore(conn, account_hash)
    classifier = LLMClassifier(cfg.classifier.endpoint, cfg.classifier.model)
    return store, classifier, build_journal(cfg.journal)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nucleon", description="Nucleon email engine")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_ in (
        ("run", "process the mailbox window once"),
        ("reconcile", "mark drafted entries as sent"),
    ):
        p = sub.add_parser(name, help=help_)
        p.add_argument("--config", default="config.yaml", metavar="PATH",
                       help="path to config file (default: config.yaml)")
    return parser


def main(argv: list[str], *, deps: Deps = build_default_deps) -> int:
    args = _parser().parse_args(argv)
    try:
        cfg = load_config(args.config)
    except FileNotFoundError:
        print(f"nucleon: config file not found: {args.config}", file=sys.stderr)
        return 2
    except (ValidationError, ValueError, OSError) as exc:
        print(f"nucleon: invalid config {args.config}: {exc}", file=sys.stderr)
        return 2
    try:
        store, classifier, journal = deps(cfg)
        if args.command == "run":
            r = run_once(store, classifier, journal, cfg)
            print(f"processed={r.processed} skipped={r.skipped} labeled={r.labeled} "
                  f"drafted={r.drafted} trashed={r.trashed}")
        else:
            print(f"reconciled={reconcile(store, journal, cfg)}")
    except Exception as exc:  # handled error: short message, no traceback
        print(f"nucleon: error: {exc}", file=sys.stderr)
        return 1
    return 0


def cli() -> None:
    raise SystemExit(main(sys.argv[1:]))
