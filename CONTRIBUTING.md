# Contributing to Nucleon

Thanks for your interest. Nucleon is a privacy-first email-automation engine;
contributions that keep it **small, safe, and account-neutral** are very
welcome.

## Development setup

- Python ≥ 3.11
- `pip install -e ".[dev]"`
- Run the tests: `pytest -q -W error` — the output must be pristine (no
  warnings).

## Design principles (please preserve these)

- **The gate is the security boundary.** The language model's output is
  untrusted; it reaches the system only as a strictly validated `Classification`
  (see `parse_classification`), and the deterministic gate — never the model's
  free text — decides which actions run.
- **Drafts-only by default.** Nucleon has no send path in v0.1. Replies are
  staged as native drafts; the human sends them from their own client.
- **Deletion is a reversible move to Trash**, opt-in and off by default. Never a
  permanent delete / expunge.
- **No raw mail is persisted.** The journal stores metadata and hashes only — no
  bodies, subjects, or addresses.
- **Account-neutral.** The engine holds no account identity, credentials, or
  data; everything comes from configuration. `config.example.yaml` contains only
  placeholders.
- **Minimal dependencies** (smaller attack surface): standard library first;
  runtime dependencies are only `pydantic` and `PyYAML`.

## Making changes

- Write tests first where practical; tests should verify real behaviour, not
  mocks.
- Keep files focused and single-responsibility.
- If your change touches a safety invariant above, add a test that pins it.
- Open a pull request describing what changed and why.

## Architecture

The engine is composed behind small interfaces so each part is testable in
isolation:

- `config` → typed configuration
- `models` → shared data types (and the closed `Action` union)
- `mailstore` → the `MailStore` protocol (+ a fake, and the real `ImapMailStore`)
- `quarantine` → the `Classifier` protocol, strict output validation, and the
  real `LLMClassifier`
- `gate` → the deterministic policy
- `actions` → executes allowed actions
- `journal` → the opt-in, metadata-only audit log (pluggable storage backend)
- `engine` → orchestration (`run_once`, `reconcile`)
- `cli` → the `nucleon` command

See [docs/CONCEPT.md](docs/CONCEPT.md) for the full design and threat model.

## Repository language

English.
