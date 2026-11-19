# proton-drive-kit

Dependency-light (stdlib-only, Python 3.11+) toolkit to read a **Proton Drive**
through its CLI and answer *"is this file (by sha1) already in the Drive?"* — the
core of Nucleon's Drive attachment-presence check and the shared Drive layer for
SGB_IX_BASE (design: `docs/v0.3/drive-seam-design.md`, "Weg 1").

> **Staging note:** this lives under `packages/` inside the Nucleon repo for now.
> Target is its own repo `Galil3o/proton-drive-kit`; keep it self-contained (no
> imports from `nucleon`) so the extraction is a move, not a rewrite.

## Modules
- `records` — `parse_entry` / `parse_listing` turn one CLI JSON entry into a
  `DriveRecord` (uid, path, is_folder, **claimed** sha1 + `sha1_verified`, size,
  mtime, media_type, shared_by_url).
- `datamap` — `DataMap` indexes files by claimed sha1; `contains_sha1(sha1, under=…)`
  returns the paths where that content already lives (optionally scoped to a subtree).
- `access` — `DriveAccess.list_dir(abspath)` wraps `proton-drive filesystem list -j`
  behind an injectable runner (default shells out via the dbus/keyring wrapper).
- `scanner` — `scan(access, root, max_depth=…)` BFS-yields every record under a root.

## Usage
```python
import proton_drive_kit as pdk
access = pdk.DriveAccess()                     # default runner (live CLI)
m = pdk.DataMap(); m.add_all(pdk.scan(access, "/my-files/CASES"))
hits = m.contains_sha1(attachment_sha1)        # [] = not present; [paths] = already there
```

## Caveats
- The CLI's sha1 is **client-claimed** (`sha1Verified` usually false) — a strong
  presence hint, not a verified hash. Treat matches as "very likely already present".
- Tests use synthetic fixtures (no real Drive, no PII). Run:
  `python -m pytest packages/proton-drive-kit/tests -q`.
