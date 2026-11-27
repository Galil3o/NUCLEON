# NUCLEON — Backlog (deliberately deferred work)

Single durable ledger for work that was **consciously deferred, not forgotten**.
Git is the source of truth; a chat message or an inbox entry scrolls away, this file
does not. Anything parked "for later" belongs here with enough detail that a later
session can pick it up cold. Remove an item only when it is actually done (and say where).

---

## R-030 (lint half) · ruff gate with a curated ruleset

**Status:** deferred (the *security* half — a bandit medium+ gate — shipped in `040c70a`).
**Why deferred:** a full ruff gate means a large, noisy auto-fix diff plus a ruleset
decision; it belongs in its own focused PR, not mixed into the archive-safety batch.

**Scope measured 2026-11-27** (`ruff check src packages`, ruff 0.17.6): **124 findings**,
88 auto-fixable. Breakdown:
- `UP*` modernization ~73 (`Optional[X]`→`X | None`, `datetime.utcnow`, quoted annotations) — safe auto-fix, big diff.
- `I001` unsorted-imports ×14 — auto-fix.
- `BLE001` blind-except ×13 — this codebase uses `except Exception:` **deliberately** at trust boundaries (runner normalization, `deleter.trash` isolation); likely keep (per-line `# noqa` with reason, or exclude the rule).
- `B023` function-uses-loop-variable ×7 — **all in `engine.py:126–136`, verified FALSE POSITIVE** (the `_intent`/`_appended` closures are consumed synchronously inside `actions_mod.execute()` in the same iteration; they never outlive it). Suppress with justification, do not "fix".
- `F401` unused-import ×4, plus a handful of `FURB192`/`UP031`/`S110`/`S112`/`RUF022`/`SIM113`/`FLY002`/`PLW1510`.

**Plan / definition of done:** add `[tool.ruff]` to `pyproject.toml` with a curated
`select` (at least `F`, `B`; modernization `UP` + `I` import-sort if we accept the diff),
ignore/justify `BLE001` and the `engine.py` `B023` cluster; run `ruff check --fix` for the
safe set and hand-fix `F401`; add a `ruff check` step to the `security`/lint CI job so it
gates from then on. Green `ruff check` in CI = done.

---

## Parse part/byte caps (mail attachment extraction) · defense-in-depth

**Status:** deferred (low marginal risk; no specific reviewer finding — this was a
maintainer open-item, not a confirmed bug).
**Why low-risk:** the raw message handed to `presence.extract_attachments` /
`mailfetch.parse.extract_with_payload` is already byte-bounded by the fetch budget
(`LimitsConfig.max_message_bytes`), so part count and decoded bytes are already bounded
by the input size.
**Plan if taken:** add a part-count cap and a cumulative decoded-byte cap to both extractors,
with limits **derived from `LimitsConfig`** (never invented), and mark the extraction result
**truncated** when a cap trips (do not silently drop attachments — same honesty contract as
everywhere else). Needs owner go on whether it's worth the pure-function API change.
**Definition of done:** both extractors cap parts + decoded bytes from config and signal
truncation; tests cover the cap trip.

---

## R-028 · journal index

**Status:** open feature, not started. **Plan:** needs a short design pass first (what the
index keys on, how it's rebuilt, durability); likely a reviewable draft (like
`drive-increment`) before integration. Carry the design into `docs/design/` when taken.

---

## Producer-wiring · realize the honesty contract end-to-end

**Status:** open; the next build step after the differ/DataMap contracts.
The differ (`drive-increment`, PRIVATE_TOOLS) and the presence `DataMap` now **require** the
map producer to declare what it scanned, else they conservatively report INCOMPLETE/unknown.
The producer (the drive-scan → TSV / `DataMap` builder) must:
- emit an `ERR` row for every `ScanIncomplete` marker the kit scanner yields (gaps), and
- emit a root `COMPLETE` row (increment TSV) / call `add_all(covered=root)` (`DataMap`) when a
  scan finishes, declaring the positively-covered scope.
Until wired, REMOVED (differ) and `absent` (presence) never fire — safe, but not yet useful.
Touches operational drive-scan tooling + live data → do under supervision, with live acceptance.

---

## main consolidation · gated on review

**Status:** NEEDS-OWNER + a fresh BOFH pass on the latest `claude/v0.3-drive-seam` HEAD.
Merge `claude/v0.2` and `claude/v0.3-drive-seam` cleanly to `main` and tag (v0.2.0 /
v0.3.0-alpha) once the owner says go and the reviewer has re-checked the post-batch commits.

---

## Notes (not open work)

- Push-time "repository moved" notices are a harmless case redirect (`nucleon`→`NUCLEON`,
  `pingpong`→`PingPong`); pushes succeed via the redirect. `git remote set-url` to the
  canonical casing did not persist in these checkouts, so the notice recurs — cosmetic only.
