"""Synthetic Proton Drive CLI entries matching the real JSON shape (no real PII)."""

FILE_ENTRY = {
    "uid": "UID-FILE-1",
    "parentUid": "UID-PARENT",
    "name": {"ok": True, "value": "Atomic Mail.txt"},
    "type": "file",
    "mediaType": "text/plain",
    "isSharedByUrl": False,
    "modificationTime": "2026-04-10T09:41:08.000Z",
    "totalStorageSize": 125,
    "activeRevision": {
        "storageSize": 125,
        "claimedDigests": {
            "sha1": "a7f4db705d7cf4b7b378395a3e4c2f2e71cdc9fd",
            "sha1Verified": False,
        },
    },
}

FOLDER_ENTRY = {
    "uid": "UID-FOLDER-1",
    "parentUid": "UID-PARENT",
    "name": {"ok": True, "value": "COURT - LABOR"},
    "type": "folder",
    "isSharedByUrl": False,
    "modificationTime": "2026-04-10T09:40:59.000Z",
}

FILE_PLAIN_NAME = {**FILE_ENTRY, "uid": "UID-FILE-2", "name": "Plain.txt"}

FILE_NO_REVISION = {k: v for k, v in FILE_ENTRY.items() if k != "activeRevision"}
FILE_NO_REVISION["uid"] = "UID-FILE-3"
FILE_NO_REVISION["name"] = {"ok": True, "value": "NoRev.bin"}

LISTING = [FOLDER_ENTRY, FILE_ENTRY]
