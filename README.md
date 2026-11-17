# NUCLEON

[![CI](https://github.com/Galil3o/NUCLEON/actions/workflows/ci.yml/badge.svg)](https://github.com/Galil3o/NUCLEON/actions/workflows/ci.yml)

Self-hosted, privacy-first email automation for Proton Mail (Bridge/IMAP). Every message is treated as untrusted: a quarantined LLM reads it with no tool access, and a deterministic gate auto-runs reversible actions but holds irreversible ones (send, delete) for approval. Prompt-injection resistant by design.

## Status

Early **v0.1** — the headless engine is implemented and tested against in-memory
fakes; **live acceptance against a real Proton Bridge / local model is not yet
done** (see Roadmap). See **[docs/CONCEPT.md](docs/CONCEPT.md)** for the design
and the reasoning behind it — including the threat model (the "lethal trifecta")
that led to the drafts-only approval model — and
**[docs/plans/2026-11-17-nucleon-v0.1.md](docs/plans/2026-11-17-nucleon-v0.1.md)**
for the implementation plan.

This repository is maintained in English.

## How it works

```
read-only IMAP fetch → quarantined classifier → deterministic gate → actions
                        (no tools, strict           (reversible: auto)
                         output validation)          (irreversible: you)
```

- A **quarantined** language model reads each message. It has no tool access and
  returns only a strictly validated `Classification` — its free text can never
  itself trigger an action.
- A **deterministic gate** decides what happens, branching only on validated
  fields: it auto-applies reversible labels, and stages a reply **draft**.
- **Nucleon never sends or permanently deletes on its own.** Replies are staged
  as **native drafts** for you to review and send from your own client (Proton
  web / mobile / Outlook via Bridge). Cleanup is an **opt-in, off-by-default**
  move to **Trash** (reversible) — never a permanent delete.
- An **opt-in mail journal** records metadata and hashes only (never message
  bodies, subjects, or addresses) for auditability, reconciliation, and
  idempotency.

The security posture does not depend on the model resisting prompt injection:
the model is assumed untrusted, and the **capabilities are the boundary**.

## Install

Requires Python ≥ 3.11.

```bash
pip install -e ".[dev]"   # editable install with the test extra
```

Runtime dependencies are only `pydantic` and `PyYAML`.

## Configure

Copy the example and fill in the placeholders:

```bash
cp config.example.yaml config.yaml
```

The IMAP password is **never** read from the config file — it comes from the
`NUCLEON_IMAP_PASSWORD` environment variable only.

## Run

```bash
nucleon run --config config.yaml        # one pass: classify, label, stage drafts
nucleon reconcile --config config.yaml  # mark staged drafts that have been sent
```

> v0.1 note: connecting to a real Proton Bridge still needs live setup (Bridge's
> TLS certificate, draft/label behaviour) that is not yet verified — see Roadmap.
> The engine is fully exercised by the test suite against fakes.

## Safety model (brief)

- **No send path** in v0.1 (drafts-only; you send natively).
- **Deletion is move-to-Trash only** — reversible, opt-in, off by default; never
  a permanent expunge.
- **Quarantined model**: output is strictly validated; the gate acts only on
  typed fields, so injected instructions in a message change nothing.
- **No raw mail persisted**: the journal is metadata/hashes only.
- **Account-neutral**: the engine holds no identity, credentials, or data.
- **Minimal dependencies** to keep the attack surface small.

## Roadmap (not in v0.1, by design)

Live Proton Bridge/model acceptance; per-message error isolation for live runs;
an optional, opt-in send path; a web/API frontend; non-Proton providers; and
remote journal backends (a private Git repo, a cloud drive) behind the existing
storage seam.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Issues and pull requests — including
challenges to the design itself — are welcome.

## License

[Apache-2.0](LICENSE).
