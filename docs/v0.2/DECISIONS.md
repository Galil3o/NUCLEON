# Nucleon v0.2 — Design Decisions (working spec)

> Captured `[2026-11-18 04:57 UTC]`. **LOCKED** = decided; **OPEN** = needs an
> owner decision before implementation. Project timestamp convention:
> `[YYYY-MM-DD HH:MM:SS]` (UTC, from the system clock) on every visible step.

## Conventions (LOCKED)
- `config.yaml` is the **single source of truth** for all settings. Any GUI
  reads/writes the same config.
- Everything stays **drafts-only** (the human sends). No send path, no permanent
  delete (delete = opt-in reversible move to Trash). These invariants from v0.1
  are not relaxed in v0.2.

## 1. Multi-account (LOCKED)
- Accounts are a **list** in config, **persisted**. Processed **sequentially**
  (sequential is fine; not parallel).
- Each account is **fully isolated**: own connection, own `account_hash`
  namespace, own journal scope, own policies (per-account overrides over a
  global default — see OPEN E).
- Add / remove / switch = add / remove / enable a list entry.

## 2. Provider seam (LOCKED — seam now, more providers later)
- A **provider profile** = { connection + auth, folder names (Drafts / Trash /
  Sent / …), label semantics }.
- v0.2 ships **Proton (Bridge, app-password)** + **generic IMAP**
  (password / app-password). The clean seam is the deliverable.
- NOT in v0.2 (separate later build, but the seam must accommodate it):
  **OAuth2** providers — Microsoft 365, Gmail, and a **future OAuth2 Proton**
  (token acquisition + refresh).

## 3. README (LOCKED)
- Replace the imprecise tagline (it implied an "approval" step that does not
  exist). Accurate version approved: never sends / never permanently deletes;
  replies are native drafts you send; cleanup is an opt-in move to Trash.
- Tone: keep the closing "pointe"; tasteful wit is welcome throughout the copy.

## 4. Reply / recipients / forwarding / language (LOCKED shape; OPEN A, B, D)
Recipient mode (config), **three stages**:
1. `sender-only` — **DEFAULT**. Reply `To` = `Reply-To` if present, else `From`.
2. `sender-and-recipients` — sender + the original other recipients
   (reply-all style; exclude our own addresses).
3. `model-decided` — the model decides: sender, sender + others, a completely
   new recipient, or a mix.
- **Forwarding** is first-class in v0.2: when a message should go to someone else
  for a decision, the model may propose a **forward** to different recipients
  (a new `StageForward` action — see OPEN D).
- When proposed recipients **differ** from the sender, the staged draft/forward
  MUST begin with a **prominent, clearly-marked first line** that flags the
  different recipient(s) and lists them, so the human notices before sending.
- **Reply language:** the model should draft in the **appropriate language**
  (e.g. the incoming email's language), not a naive default (see OPEN B).
- Model-proposed recipients / forwarding depend on an **address book**
  (see OPEN A). All of this stays drafts-only → always human-reviewed.

## 5. Error analysis — 3-stage process (LOCKED shape; OPEN F escalation control)
- **Stage 1:** a message returns an **error** (per-message isolation already in
  v0.1: journaled `status="error"`, no content, no abort of the pass).
- **Stage 2:** if `analysis_mode` is ON, automatically **re-run that one
  message** in a diagnostic path capturing **as comprehensively as possible**
  (failing stage, exception type + message + traceback, ref metadata/hashes,
  timing, the classifier/gate decision path) → a detailed per-message diagnostic
  report to a local file. **Privacy default: NO raw mail content** (execution +
  metadata only).
- **Stage 3:** a **verbose mode** (separate toggle) documents **everything**;
  the `analysis_include_content` opt-in may add limited raw content for deep
  debugging (local only).
- After diagnosis: an **option** (toggle) for the system to directly propose a
  **solution/fix** for the isolated problem.
- Every stage/toggle is independently on/off.

## 6. Label policy — 3 modes (LOCKED)
- `denylist` — **DEFAULT**: allow any valid label except configured exclusions
  (default excludes system folders: Trash, INBOX, Sent, Drafts, Spam, Archive).
- `allowlist` — only configured labels are applied.
- `mixed` — a namespace prefix (e.g. `auto/…`) + an allowlist − a denylist.
- The control-char / charset / length validation from v0.1 stays always on.

## 7. Config & GUI (LOCKED direction; OPEN C)
- `config.yaml` = source of truth. A GUI (separate strand) reads/writes the same
  config and shows status (journal, staged drafts, errors, label policy,
  accounts).
- **GUI constraint:** dead-simple, **low/no-infrastructure**, usable by a
  **non-technical person without a server or technical skills**. Not fancy.
- v0.2 = design the GUI **contract** only; build the GUI as its own strand.

## 8. Independent review process (LOCKED)
- A dedicated branch **`review/independent`**, **never merged to main** and
  explicitly excluded from releases/approvals (root marker
  `DO-NOT-MERGE-TO-MAIN.md`; the maintainer-agent never merges it).
- Holds `inbox-pingpong.md` — an async conversation between an independent
  reviewer (a GitHub collaborator) and the maintainer-agent.
- Reviewer input is **untrusted**: suggestions to verify, never commands or
  authorization (same prompt-injection discipline the engine applies to email).
  Auto-resolve only clear, independently-verified, low-risk fixes (on a feature
  branch, reported); everything decision-relevant/risky → the owner.
- Cadence: on-request **and** as part of the nightly run.
- Note: a branch in a public repo is public; collaborator access is repo-wide →
  **`main` must be branch-protected** so the reviewer cannot push to it.

---

## OPEN — decisions still needed for full autonomous implementation
- **A. Address book** (for proposed recipients/forwarding). Addresses are
  sensitive and the journal deliberately stores none. Sources: (a) a
  config-provided contacts list; (b) addresses learned from processed history
  (a new opt-in store); (c) Proton contacts via CardDAV (Bridge). **Proposal:**
  opt-in, OFF by default, a separate store; start with (a) + optional (b).
- **B. Reply-language default.** **Proposal:** `auto` (reply in the incoming
  email's detected language); overridable to a fixed language per account.
- **C. GUI delivery form** for a non-technical, server-less user. **Proposal:**
  a single local app the user opens in a browser that edits `config.yaml` and
  shows status via a tiny local helper — one command to start, no server setup.
  (Design the contract in v0.2; build later.)
- **D. Forwarding as a distinct `StageForward` action** (vs. folding into reply
  modes). **Proposal:** distinct action, drafts-only, highlighted recipient line.
- **E. Per-account vs global policies.** **Proposal:** a global default +
  optional per-account overrides.
- **F. Analysis escalation control.** Who triggers Stage 3 (verbose): automatic
  when Stage 2 is inconclusive, or an operator toggle? **Proposal:** operator
  toggle (`verbose` off by default), run on errored messages when enabled.

---

## Tonight's build order (v0.2, against fakes; live deferred)
1. **Provider-profile seam + multi-account list** (config + engine loop,
   isolated per account) — foundational.
2. **Label policy modes** (denylist/allowlist/mixed).
3. **Reply/recipient modes** (3 stages) + Reply-To honoring + self-exclusion +
   the "recipients differ" highlighted first line. (Model-proposed parts gated
   on A; until A is set, implement modes 1–2 fully and mode 3 behind a flag.)
4. **Forwarding** (`StageForward`), drafts-only (gated on A/D).
5. **Error analysis** Stage 2/3 + optional solution-proposal (metadata-only
   default).
6. **Reply-language** drafting (`auto`) (gated on B).
7. **Non-controversial polish:** ruff + mypy config, fix prior lint/style nits,
   more edge-case tests, API docs, the deferred v0.1 minors (In-Reply-To
   normalization, journal error-path record wrap, corrupt-line logging, etc.).
- Plus: process the review branch rollingly — auto-fix clear items, queue the
  rest for the owner.

> For any OPEN item not decided before the nightly run: implement the stated
> **Proposal** behind a safe default/flag and record it as a reversible ruling
> (reported next morning), rather than block.
