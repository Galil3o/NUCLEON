# Nucleon — Concept & Design

> Status: early concept / work in progress. This document explains **what**
> Nucleon is, **why** it is built this way, and **how** you can help improve it.
> Repository language is English.

## What Nucleon is

Nucleon is a **self-hosted, privacy-first email automation engine for Proton
Mail** (reached through Proton Bridge over IMAP/SMTP). It reads incoming mail
with a *quarantined* language model, classifies and labels it, and stages
reply **drafts** — while a deterministic gate decides what may run
automatically and what must stay in human hands.

Nucleon is **account-neutral**: the engine holds no account identity, no
credentials, and no mail data. A deployment supplies all of that through
configuration.

The name is a small physics joke. An incoming message is like a **proton** —
charged, potentially dangerous. Nucleon *neutralizes* it: the dangerous content
is handled under strict containment until the inbox is back to **zero**
("inbox zero" ≈ neutral charge ≈ a **neutron**). A *nucleon* is the word for
"a proton or a neutron" — the two states the system moves between.

## Threat model: why this design exists

Automating email means pointing a capable model at three things at once:

1. **Private data** — your mailbox and its history.
2. **Untrusted content** — every incoming email, written by anyone.
3. **An outbound channel** — the ability to send mail or share files.

When all three meet in one agent, you have what is widely called the **"lethal
trifecta"** (a framing popularized by Simon Willison). A crafted email can try
to instruct the model to exfiltrate your data or act on your behalf — this is
**prompt injection**, and it cannot be reliably *detected* or *filtered*. Any
classifier that tries to spot "is this an attack?" is in an arms race it loses.

Nucleon therefore does not try to detect injection. It **removes the
capability** instead. The security boundary is not *what the model reads* but
*what the system is allowed to do*, enforced deterministically — the
**dual-LLM / capability-gate** approach (see also DeepMind's *CaMeL*). Model
output is always treated as untrusted input to a deterministic gate; the gate,
not the model, authorizes actions.

## The gate: reversible vs. irreversible

- **Reversible actions** — label, move/file, archive. Run **automatically**,
  deterministically, and are undoable.
- **Irreversible actions** — send, delete. **Never** performed autonomously by
  Nucleon.
  - **Send** is delegated to the human (see "Drafts-only approval").
  - **Delete** means *trash* (reversible), never a permanent expunge, and is
    gated.

## Drafts-only approval (v0.1)

Instead of a custom "approve" button, Nucleon uses Proton's **own draft
mechanism** as the approval queue:

1. Nucleon stages the proposed reply as a **native draft** (IMAP `APPEND` with
   the `\Draft` flag and correct threading headers: `In-Reply-To`,
   `References`, `Re:` subject).
2. The draft appears **natively** in your Proton client (web, mobile, or
   Outlook via Bridge), inside the right conversation.
3. You review it and press **Send**. Your native Send **is** the approval.
   No extra interface, no context switch; edit first if you want.

**Why this is the strongest form of the gate:** in drafts-only mode Nucleon has
**no send path at all** (no SMTP). Even a fully compromised quarantined model
**cannot send anything** — the outbound leg of the lethal trifecta is simply
removed. The only entity with an outbound channel is **you**, with the message
in front of you.

Details:

- **Identification:** proposed drafts carry a label, `nucleon/proposed`, so you
  can filter "proposed by Nucleon" from your own drafts.
- **Fallback:** if Bridge-appended drafts do not render cleanly as editable,
  sendable drafts in the Proton web composer, Nucleon instead stages them in a
  dedicated **`Nucleon/Outbox`** folder (own label); sending stays native.
- **Reconciliation:** Nucleon watches the **Sent** folder **read-only** and
  marks a proposal as sent once it observes the sent copy. It never sends
  itself.
- **To verify:** Proton handles drafts specially server-side. Whether a
  Bridge-`APPEND`ed draft is cleanly editable/sendable and correctly threaded in
  the Proton web UI must be **verified against a real Bridge** before relying on
  it. Until then the Outbox fallback is the safe assumption.

## Roadmap seam: optional send (after v0.1)

v0.1 is intentionally drafts-only. The design keeps a single, explicit extension
point for a later **user-approved send path** — for people who want even lower
friction. Any such path re-introduces a send capability, so it is **opt-in, off
by default, and gated**. The default posture never weakens.

## Mail journal

The mail journal is a **built-in, opt-in capability** (part of v0.1). Every
deployment can switch it on to gain auditability, reconciliation, and
idempotency. It is an **activation seam** — a toggle on the engine, not a
separate service — and it is **off by default**. There is no shared journal;
each operator runs and owns their own.

A lightweight, append-only record of what the engine did. Per message it stores
a **reference** (an account-neutral source key / message-id hash), the **action**
(read → classified → drafted → marked-sent), a **timestamp**, and **status**.

- **Why:** it makes "safe automation" *verifiable* (auditability), enables
  reconciliation (draft → sent), and prevents double-work (idempotency: do not
  re-draft the same message).
- **Privacy:** **metadata and hashes only** — no bodies, subjects, addresses, or
  attachments. Consistent with "no raw mail is persisted."
- **Boundary:** the engine defines only the **schema**. The actual journal data
  for a deployment lives in the operator's private space, never in this
  repository.

## Architecture

```
reader (IMAP) → quarantine (LLM adapter) → gate (deterministic) → actions
                                                     │
                                              journal · config
```

- **Engine (Python).** Native IMAP/MIME/SMTP handling lives in the standard
  library, the local-LLM and structured-output ecosystem is strongest here, and
  the dependency/attack surface stays small — which matters for software that
  processes untrusted email.
- **Quarantine stage.** The model that reads untrusted content has **no tool
  access** and returns **structured data only** (never free-form commands). Its
  output is data for the gate, never instructions.
- **Config-injected & account-neutral.** The engine ships with a
  `config.example.yaml` of placeholders; it carries no identity, credentials, or
  data of its own.
- **Provider abstraction.** "Proton Bridge on a separate server" is **one**
  connection mode, not an assumption — a separate-server Bridge is the exception,
  not the rule, for most users. The mail-access layer is abstract so other modes
  (a local Bridge, a future Proton API, or generic IMAP) can be added.
- **Frontend seam (hybrid).** The engine exposes a clean local API so an
  **optional** frontend — e.g. a TypeScript/web dashboard — can be added later
  without touching the core. v0.1 is headless (CLI/daemon).

> **On the choice of Python:** projects like `inbox-zero` and `Zero` use
> TypeScript because they are **web SaaS apps** built on provider REST APIs and
> a browser UI — a different product shape. Nucleon's core is a headless,
> IMAP-based, local-model engine, where Python is the better fit. The API seam
> above keeps a future TypeScript UI a clean, hybrid add-on rather than a
> rewrite.

## v0.1 scope

**In:** read-only IMAP fetch → quarantined classification + draft → automatic
labeling (reversible) → stage draft (native Drafts, or `Nucleon/Outbox`
fallback) → read-only reconciliation via Sent. Includes the opt-in **mail
journal** (metadata/hashes only, off by default). Local models supported.

**Out, by design (seams left for later):** SMTP/sending, deletion, a web UI,
non-Proton providers.

## Contributing

We document decisions **and their reasoning** so others can follow what was done
and why, and propose improvements. Issues and pull requests are welcome,
including challenges to the design itself. The repository is maintained in
English.
