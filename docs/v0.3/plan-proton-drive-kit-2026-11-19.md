# proton-drive-kit Foundation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A dependency-light, test-driven Python package that reads a Proton Drive via its CLI, turns listings into typed records, and answers "is this sha1 already in the Drive?" (the core of the attachment-presence check).

**Architecture:** Four focused modules behind clean seams — `records` (parse one CLI JSON entry → `DriveRecord`), `datamap` (records + sha1→paths index + presence lookup), `access` (wrap `proton-drive filesystem list -j <path>` behind an injectable runner), `scanner` (BFS over `access`). Tests inject fakes/fixtures; no live Drive in tests.

**Tech Stack:** Python 3.11+, stdlib only (`dataclasses`, `json`, `subprocess`, `typing`). No pydantic, no new deps.

**Spec:** `docs/v0.3/drive-seam-design.md` (design), `docs/v0.3/drive-recon-2026-11-19.md` (real CLI contract + taxonomy).

## Global Constraints
- Staged in-repo at `packages/proton-drive-kit/` on branch `claude/v0.3-drive-seam`; future extraction to its own repo (Weg 1) — keep it self-contained (own package dir + tests + README), no imports from `nucleon`.
- stdlib only; Python 3.11+.
- `sha1` is the CLI's **claimed** digest (`activeRevision.claimedDigests.sha1`, often `sha1Verified: false`) — store it and the `sha1_verified` flag; never present it as a verified hash.
- No file **contents** are ever read or stored — metadata only.
- Tests use **synthetic** fixtures matching the real JSON shape (no real names/paths/PII).
- Run tests: `python -m pytest packages/proton-drive-kit/tests -q`.

## Review Focus
- **`name` as plain string vs `{"ok":true,"value":...}` dict** — parser must accept both (Task 1).
- **File without sha1 / without `activeRevision`** (and folders) — `sha1=None`, no crash; presence lookup never matches on empty/None sha1 (Tasks 1, 2).
- **Same sha1 at multiple paths** — `paths_for_sha1` returns ALL, not the last (Task 2).
- **CLI returns an error object or non-list JSON** — `access.list_dir` raises `DriveAccessError`, not an unhandled exception (Task 3).
- **A folder whose name contains `/`** — path assembly must not silently create a bogus deeper path; scanner records the raw name and does not split on it (Tasks 1, 4).

---

### Task 1: Record parser (`records.py`)

**Files:**
- Create: `packages/proton-drive-kit/proton_drive_kit/records.py`
- Create: `packages/proton-drive-kit/tests/test_records.py`
- Create: `packages/proton-drive-kit/tests/fixtures.py` (synthetic entries)

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class DriveRecord` with fields: `uid: str`, `parent_uid: str | None`, `name: str`, `path: str`, `is_folder: bool`, `sha1: str | None`, `sha1_verified: bool`, `size: int | None`, `mtime: str | None`, `media_type: str | None`, `shared_by_url: bool`.
  - `def parse_entry(entry: dict, parent_path: str = "") -> DriveRecord` — `path` = `f"{parent_path}/{name}"` when `parent_path` else `name`; `name` read from `entry["name"]` (`.get("value")` if dict else the value); `is_folder` = `entry.get("type") == "folder"`; `sha1`/`sha1_verified` from `entry["activeRevision"]["claimedDigests"]` when present else `None`/`False`; `size` from `totalStorageSize` or `activeRevision.storageSize`; `mtime` = `(entry.get("modificationTime") or "")[:19] or None`; `media_type` from `mediaType`; `shared_by_url` from `isSharedByUrl`.
  - `def parse_listing(data, parent_path: str = "") -> list[DriveRecord]` — accepts a JSON `str` or a parsed `list`; raises `ListingError` if it is not a list or is an error object.
  - `class ListingError(Exception)`.

- [ ] **Step 1: Write failing tests** in `test_records.py` (fixtures in `fixtures.py`):
  - `test_parses_file_entry_with_claimed_sha1`: a file entry → `is_folder False`, `sha1 == "a7f4..."`, `sha1_verified False`, `size == 125`, `media_type == "text/plain"`, `path == "/my-files/Atomic Mail.txt"` given `parent_path="/my-files"`.
  - `test_parses_folder_entry`: folder entry → `is_folder True`, `sha1 is None`.
  - `test_name_accepts_plain_string`: entry with `"name": "Plain.txt"` → `name == "Plain.txt"`.
  - `test_file_without_active_revision`: file entry lacking `activeRevision` → `sha1 is None`, no raise.
  - `test_parse_listing_rejects_non_list`: `parse_listing('{"error":"x"}')` raises `ListingError`.
- [ ] **Step 2: Run, verify fail** — `python -m pytest packages/proton-drive-kit/tests/test_records.py -q` → fail (module missing).
- [ ] **Step 3: Implement** `DriveRecord`, `parse_entry`, `parse_listing`, `ListingError` in `records.py` per the signatures above.
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** (`feat(pdk): record parser for Proton Drive CLI entries`).

---

### Task 2: Data-map + presence lookup (`datamap.py`)

**Files:**
- Create: `packages/proton-drive-kit/proton_drive_kit/datamap.py`
- Create: `packages/proton-drive-kit/tests/test_datamap.py`

**Interfaces:**
- Consumes: `DriveRecord` (Task 1).
- Produces:
  - `class DataMap` with: `add(rec: DriveRecord) -> None` (indexes only files with a non-empty `sha1`), `add_all(recs: Iterable[DriveRecord]) -> None`, `paths_for_sha1(sha1: str) -> list[str]` (all file paths with that sha1, insertion order, `[]` if none/empty), `contains_sha1(sha1: str, under: str | None = None) -> list[str]` (as `paths_for_sha1` but, when `under` is set, only paths that start with `under`), `records() -> list[DriveRecord]`, `__len__` (number of indexed files).

- [ ] **Step 1: Write failing tests** in `test_datamap.py`:
  - `test_presence_hit_returns_path`: add a file rec (sha1 S, path P) → `contains_sha1(S) == [P]`.
  - `test_presence_miss_returns_empty`: `contains_sha1("nope") == []`.
  - `test_empty_or_none_sha1_never_indexed`: adding a folder and a file with `sha1=None` → `len == 0`, `contains_sha1("") == []`.
  - `test_same_sha1_multiple_paths`: two files, same sha1, paths P1,P2 → `paths_for_sha1(S) == [P1, P2]`.
  - `test_under_scopes_by_prefix`: files under `/a` and `/b` with same sha1 → `contains_sha1(S, under="/a") == ["/a/x"]`.
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement** `DataMap` per signatures (dict `sha1 -> list[str]`, preserve insertion order; keep a flat `list[DriveRecord]`).
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** (`feat(pdk): data-map with sha1 presence lookup`).

---

### Task 3: Access layer (`access.py`)

**Files:**
- Create: `packages/proton-drive-kit/proton_drive_kit/access.py`
- Create: `packages/proton-drive-kit/tests/test_access.py`

**Interfaces:**
- Consumes: `parse_listing`, `DriveRecord`, `ListingError` (Task 1).
- Produces:
  - `Runner = Callable[[str], str]` (takes an abspath, returns the CLI's raw JSON stdout; raises on process failure).
  - `class DriveAccessError(Exception)`.
  - `class DriveAccess` — `__init__(self, runner: Runner | None = None, *, wrapper: str = "/home/claude/drive_list.sh")`; `list_dir(self, abspath: str) -> list[DriveRecord]` calls `runner(abspath)`, then `parse_listing(out, parent_path=abspath)`; wraps any `ListingError`/runner exception in `DriveAccessError`. Default runner shells out: `subprocess.run(["bash", wrapper, abspath], capture_output=True, text=True, timeout=120)`, returns `stdout`, raises on non-zero.

- [ ] **Step 1: Write failing tests** in `test_access.py` (inject a fake runner; never shell out):
  - `test_list_dir_parses_fixture`: fake runner returns a two-entry fixture JSON → `list_dir("/my-files")` returns 2 `DriveRecord`s with `path` prefixed by `/my-files`.
  - `test_runner_error_becomes_access_error`: fake runner raises → `DriveAccessError`.
  - `test_error_object_becomes_access_error`: fake runner returns `'{"error":"nope"}'` → `DriveAccessError`.
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement** `DriveAccess`, `Runner`, `DriveAccessError` per signatures; default runner as above (not exercised in tests).
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** (`feat(pdk): Drive access layer over the CLI (injectable runner)`).

---

### Task 4: BFS scanner (`scanner.py`)

**Files:**
- Create: `packages/proton-drive-kit/proton_drive_kit/scanner.py`
- Create: `packages/proton-drive-kit/tests/test_scanner.py`

**Interfaces:**
- Consumes: `DriveAccess.list_dir` → `list[DriveRecord]` (Task 3), `DriveRecord` (Task 1).
- Produces:
  - `def scan(access, root: str, *, max_depth: int = 12) -> Iterator[DriveRecord]` — yields every record returned under `root` (folders and files) in BFS order; for each folder record, recurses by calling `access.list_dir(<folder path>)` until `max_depth`. Uses each record's own `path` as the next list target. A `DriveAccessError` on a subfolder is skipped (that subtree is not expanded) and scanning continues.

- [ ] **Step 1: Write failing tests** in `test_scanner.py` with a `FakeAccess` (maps path → list[DriveRecord], built from `parse_listing` on synthetic fixtures):
  - `test_scans_one_level`: root with 1 file + 1 folder (folder empty) → yields both records.
  - `test_recurses_into_folders`: root→folder→file → the nested file is yielded with its full path.
  - `test_max_depth_stops_recursion`: `max_depth=1` → nested folder's children not listed.
  - `test_subfolder_access_error_is_skipped`: a folder whose `list_dir` raises → scan still yields the rest, no exception.
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement** `scan` per signature (BFS queue of folder paths + depths; guard `DriveAccessError`).
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** (`feat(pdk): BFS Drive scanner over the access layer`).

---

### Task 5: Package wiring + end-to-end (fixtures) test

**Files:**
- Create: `packages/proton-drive-kit/proton_drive_kit/__init__.py` (re-export `DriveRecord`, `parse_entry`, `parse_listing`, `DataMap`, `DriveAccess`, `scan`)
- Create: `packages/proton-drive-kit/pyproject.toml` (name `proton-drive-kit`, py311, no deps)
- Create: `packages/proton-drive-kit/README.md` (what it is, Weg-1 extraction note, usage)
- Create: `packages/proton-drive-kit/tests/test_end_to_end.py`

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Write failing test** `test_scan_builds_datamap_and_finds_attachment`: `FakeAccess` with a small tree containing a file of sha1 S at path P → `DataMap` built from `scan(...)` → `contains_sha1(S) == [P]` and `contains_sha1("absent") == []`.
- [ ] **Step 2: Run, verify fail** (import error until `__init__` re-exports).
- [ ] **Step 3: Implement** `__init__.py`, `pyproject.toml`, `README.md`.
- [ ] **Step 4: Run** the whole package suite `python -m pytest packages/proton-drive-kit/tests -q` → all pass; also run the repo suite to confirm no interference.
- [ ] **Step 5: Commit** (`feat(pdk): package wiring + end-to-end attachment-presence test`).
