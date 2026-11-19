"""Command-line entrypoint: ``nucleon run`` and ``nucleon reconcile``."""
from __future__ import annotations

import argparse
import os
import ssl
import sys
from typing import Any, Callable

from pydantic import ValidationError

from nucleon.accounts import AccountIdentityChanged, AccountRegistry, account_hash_for
from nucleon.config import Config, load_config
from nucleon.analysis import AnalysisLog
from nucleon.engine import reconcile, run_once
from nucleon.journal import build_journal
from nucleon.marker import build_marker
from nucleon.recipients import ContactBook, FileLearnedStore

# Exit status (documented in the README):
#   0  everything ok (a message that vanished mid-run is benign and counts as ok)
#   1  runtime error (adapter/server/preflight failure; no usable result)
#   2  configuration error (missing/invalid/unparseable config, account identity)
#   3  the run completed but at least one message failed (errors) or was rejected
#      by a size budget (over_budget); successful actions are still all reported
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_CONFIG = 2
EXIT_RUN_FAILURES = 3

Deps = Callable[[Config], tuple[Any, Any, Any]]


def make_tls_context(cfg: Config) -> ssl.SSLContext:
    """TLS context for IMAP. Verification is never disabled; an optional
    ``mail.tls_ca_file`` (e.g. the Proton Bridge CA) replaces the system CAs."""
    if cfg.mail.tls_ca_file:
        return ssl.create_default_context(cafile=cfg.mail.tls_ca_file)
    return ssl.create_default_context()


def build_classifier(cfg: Config):
    """Construct the quarantined LLM classifier (provider seam).

    The classifier speaks the OpenAI-compatible chat protocol, so the same seam
    serves a local keyless model, Claude or Gemini. An API key (needed only for a
    keyed/remote provider) comes ONLY from NUCLEON_CLASSIFIER_API_KEY — never from
    config or the URL (R-007). A loopback keyless endpoint needs none.
    """
    from nucleon.quarantine.llm import LLMClassifier

    return LLMClassifier(
        cfg.classifier.endpoint, cfg.classifier.model,
        api_key=os.environ.get("NUCLEON_CLASSIFIER_API_KEY"),
        max_response_bytes=cfg.limits.max_model_response_bytes,
    )


def build_default_deps(cfg: Config) -> tuple[Any, Any, Any]:
    """Wire the real adapters. Needs a live IMAP server; the password comes
    only from the NUCLEON_IMAP_PASSWORD environment variable."""
    import imaplib

    from nucleon.mailstore.imap import ImapMailStore

    password = os.environ.get("NUCLEON_IMAP_PASSWORD")
    if not password:
        raise RuntimeError("NUCLEON_IMAP_PASSWORD is not set")
    # Always use TLS so the password never travels in cleartext. Proton Bridge's
    # self-signed cert is trusted via mail.tls_ca_file; verification is never disabled.
    context = make_tls_context(cfg)
    if cfg.mail.port == 993 or "ssl" in cfg.mail.mode.lower():
        conn = imaplib.IMAP4_SSL(cfg.mail.host, cfg.mail.port, ssl_context=context)
    else:
        conn = imaplib.IMAP4(cfg.mail.host, cfg.mail.port)
        conn.starttls(ssl_context=context)
    conn.login(cfg.mail.username, password)
    account_hash = account_hash_for(cfg)  # R-012: bound to the stable id, not the login
    lim = cfg.limits
    store = ImapMailStore(
        conn, account_hash, max_message_bytes=lim.max_message_bytes,
        max_body_bytes=lim.max_body_bytes, max_header_bytes=lim.max_header_bytes,
    )
    classifier = build_classifier(cfg)
    return store, classifier, build_journal(cfg.journal)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nucleon", description="Nucleon email engine")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_ in (
        ("run", "process the mailbox window once"),
        ("reconcile", "match Sent against open drafts (sent / observed reply)"),
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
        return EXIT_CONFIG
    except (ValidationError, ValueError, OSError) as exc:
        print(f"nucleon: invalid config {args.config}: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    try:
        AccountRegistry(cfg.registry_path).check(cfg)
    except AccountIdentityChanged as exc:
        print(f"nucleon: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    exit_code = EXIT_OK
    try:
        store, classifier, journal = deps(cfg)
        if args.command == "run":
            analysis = AnalysisLog(cfg.analysis.path) if cfg.analysis.enabled else None
            # R-009: account-bound contacts; the learned store is read only when opted in.
            book = ContactBook.from_config(
                cfg, FileLearnedStore(cfg.recipients.learned_path)
                if cfg.recipients.learned_enabled else None)
            r = run_once(store, classifier, journal, cfg, build_marker(cfg.marker), analysis,
                         contacts=book)
            print(f"processed={r.processed} skipped={r.skipped} labeled={r.labeled} "
                  f"drafted={r.drafted} trashed={r.trashed} errors={r.errors} "
                  f"vanished={r.vanished} over_budget={r.over_budget}")
            for ev in r.budget_events:  # per-message visibility; no content, pseudonym only
                print(f"budget: kind={ev.kind} outcome={ev.outcome} limit={ev.limit} "
                      f"observed={ev.observed} ref={ev.ref_pseudonym}")
            if r.errors or r.over_budget:  # R-014: failures must not look like success
                exit_code = EXIT_RUN_FAILURES
        else:
            rep = reconcile(store, journal, cfg)
            print(f"reconciled={rep.sent} observed={rep.observed}")
    except Exception as exc:  # handled error: short message, no traceback
        print(f"nucleon: error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    return exit_code


def cli() -> None:
    raise SystemExit(main(sys.argv[1:]))
