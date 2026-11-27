"""proton-drive-kit — read a Proton Drive via its CLI; records, data-map, scanner."""
from proton_drive_kit.access import DriveAccess, DriveAccessError, Runner
from proton_drive_kit.datamap import DataMap
from proton_drive_kit.records import (
    DriveRecord,
    ListingError,
    parse_entry,
    parse_listing,
)
from proton_drive_kit.dedup import (
    DedupDecision,
    find_duplicate_groups,
    plan_dedup,
    summarize,
)
from proton_drive_kit.scanner import scan, ScanIncomplete

__all__ = [
    "DriveRecord",
    "parse_entry",
    "parse_listing",
    "ListingError",
    "DataMap",
    "DriveAccess",
    "DriveAccessError",
    "Runner",
    "scan",
    "ScanIncomplete",
    "DedupDecision",
    "find_duplicate_groups",
    "plan_dedup",
    "summarize",
]
