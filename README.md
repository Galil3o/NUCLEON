# NUCLEON
Self-hosted, privacy-first email automation for Proton Mail (Bridge/IMAP). Every message is treated as untrusted: a quarantined LLM reads it with no tool access, and a deterministic gate auto-runs reversible actions but holds irreversible ones (send, delete) for approval. Prompt-injection resistant by design.

## Status

Early concept / work in progress. See **[docs/CONCEPT.md](docs/CONCEPT.md)** for
the design and the reasoning behind it — including the threat model (the "lethal
trifecta") that led to the drafts-only approval model.

This repository is maintained in English.
