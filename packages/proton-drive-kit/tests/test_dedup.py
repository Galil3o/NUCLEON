from proton_drive_kit.dedup import find_duplicate_groups, plan_dedup, summarize
from proton_drive_kit.records import DriveRecord

LEGAL = ("/CASES",)


def rec(path, sha1, size=10, uid=None):
    return DriveRecord(
        uid=uid if uid is not None else path, parent_uid=None,
        name=path.rstrip("/").rsplit("/", 1)[-1], path=path, is_folder=False,
        sha1=sha1, sha1_verified=False, size=size, mtime=None,
        media_type=None, shared_by_url=False)


def folder(path):
    return DriveRecord(
        uid=path, parent_uid=None, name=path.rstrip("/").rsplit("/", 1)[-1], path=path,
        is_folder=True, sha1=None, sha1_verified=False, size=None, mtime=None,
        media_type=None, shared_by_url=False)


# --- grouping / identity gate ---

def test_find_groups_ignores_folders_singletons_and_no_sha1():
    recs = [rec("/a/x.pdf", "S"), rec("/b/y.pdf", "S"), rec("/c/z.pdf", "T"),
            folder("/d"), rec("/e/n.pdf", "")]
    groups = find_duplicate_groups(recs)
    assert set(groups) == {"S"}
    assert {r.path for r in groups["S"]} == {"/a/x.pdf", "/b/y.pdf"}


def test_identity_gate_differing_size_not_grouped():
    recs = [rec("/a/x.pdf", "S", size=10), rec("/b/x.pdf", "S", size=20)]
    assert find_duplicate_groups(recs) == {}
    assert plan_dedup(recs) == []


def test_identity_gate_missing_size_not_grouped():
    recs = [rec("/a/x.pdf", "S", size=None), rec("/b/x.pdf", "S", size=None)]
    assert plan_dedup(recs) == []


# --- input de-duplication / uid safety (K2) ---

def test_repeated_record_collapsed_no_self_delete():
    r = rec("/HR/a.pdf", "S")
    assert plan_dedup([r, r]) == []


def test_shared_uid_is_never_proposed_for_deletion():
    r1 = rec("/A/x.pdf", "S", uid="U")
    r2 = rec("/B/x.pdf", "S", uid="U")
    [dec] = plan_dedup([r1, r2])
    assert all(r.uid != dec.keep.uid for r in dec.delete)
    assert all(r.uid != dec.keep.uid for r in dec.review)


# --- decision branches ---

def test_same_folder_exact_duplicates_high():
    recs = [rec("/HR/a.pdf", "S"), rec("/HR/a (1).pdf", "S"), rec("/HR/a (2).pdf", "S")]
    [dec] = plan_dedup(recs)
    assert dec.reason == "same-folder" and dec.confidence == "high"
    assert dec.keep.path == "/HR/a.pdf"
    assert {r.path for r in dec.delete} == {"/HR/a (1).pdf", "/HR/a (2).pdf"}
    assert dec.review == ()


def test_protect_keeps_legal_deletes_other_area():
    recs = [rec("/CASES/Fall/doc.pdf", "S"), rec("/PICTURES/doc.pdf", "S")]
    [dec] = plan_dedup(recs, protect_prefixes=LEGAL)
    assert dec.reason == "protect" and dec.confidence == "high"
    assert dec.keep.path == "/CASES/Fall/doc.pdf"
    assert {r.path for r in dec.delete} == {"/PICTURES/doc.pdf"}


def test_clear_copy_deletes_kopie_folder_medium():
    recs = [rec("/COMPANY/proj/report.pdf", "S"), rec("/COMPANY/Kopie/report.pdf", "S")]
    [dec] = plan_dedup(recs)
    assert dec.reason == "clear-copy" and dec.confidence == "medium"
    assert dec.keep.path == "/COMPANY/proj/report.pdf"
    assert {r.path for r in dec.delete} == {"/COMPANY/Kopie/report.pdf"}


def test_intra_area_unprotected_medium():
    recs = [rec("/PICTURES/2020/p.jpg", "S"), rec("/PICTURES/dup/p.jpg", "S")]
    [dec] = plan_dedup(recs, protect_prefixes=LEGAL)
    assert dec.reason == "intra-area" and dec.confidence == "medium"
    assert len(dec.delete) == 1 and dec.review == ()


def test_review_legal_internal_cross_folder():
    recs = [rec("/CASES/Fall A/doc.pdf", "S"), rec("/CASES/Fall B/doc.pdf", "S")]
    [dec] = plan_dedup(recs, protect_prefixes=LEGAL)
    assert dec.reason == "review" and dec.confidence == "low"
    assert dec.delete == ()
    assert len(dec.review) == 1


def test_review_cross_area_without_protection_signal():
    recs = [rec("/COMPANY/a.pdf", "S"), rec("/PERSONAL/a.pdf", "S")]
    [dec] = plan_dedup(recs, protect_prefixes=LEGAL)
    assert dec.reason == "review"


# --- protected-area safety (W4 / W5) ---

def test_legal_same_folder_extra_goes_to_review_not_delete():
    recs = [rec("/CASES/A/doc.pdf", "S"), rec("/CASES/A/doc (1).pdf", "S"),
            rec("/CASES/B/doc.pdf", "S")]
    [dec] = plan_dedup(recs, protect_prefixes=LEGAL)
    assert dec.reason == "review"
    assert dec.delete == ()  # CASES is never auto-deleted
    assert {r.path for r in dec.review} == {"/CASES/A/doc (1).pdf", "/CASES/B/doc.pdf"}


def test_clear_copy_within_protected_prefix_goes_to_review():
    recs = [rec("/CASES/Fall/doc.pdf", "S"), rec("/CASES/Kopie/doc.pdf", "S")]
    [dec] = plan_dedup(recs, protect_prefixes=LEGAL)
    assert dec.reason == "review"
    assert dec.delete == ()
    assert {r.path for r in dec.review} == {"/CASES/Kopie/doc.pdf"}


# --- copy heuristic conservatism (W3) ---

def test_copy_regex_no_false_positive_on_archiv_party_name():
    recs = [rec("/X/Archiv Mandant Müller/scan.pdf", "S"), rec("/Y/Projekt/scan.pdf", "S")]
    [dec] = plan_dedup(recs)
    assert dec.reason == "review"       # "Archiv Mandant" must NOT count as a copy
    assert dec.delete == ()


# --- parameter hygiene (N7 / N8 / prefix boundary) ---

def test_empty_copy_pattern_string_is_ignored():
    recs = [rec("/A/x.pdf", "S"), rec("/B/x.pdf", "S")]
    [dec] = plan_dedup(recs, copy_patterns=("",))
    assert dec.reason != "clear-copy"


def test_protect_prefix_without_leading_slash_still_protects():
    recs = [rec("/CASES/Fall/doc.pdf", "S"), rec("/PICTURES/doc.pdf", "S")]
    [dec] = plan_dedup(recs, protect_prefixes=("CASES",))
    assert dec.keep.path == "/CASES/Fall/doc.pdf"
    assert {r.path for r in dec.delete} == {"/PICTURES/doc.pdf"}


def test_protect_prefix_boundary_does_not_match_sibling():
    recs = [rec("/CASESX/doc.pdf", "S"), rec("/PICTURES/doc.pdf", "S")]
    [dec] = plan_dedup(recs, protect_prefixes=LEGAL)
    assert dec.reason == "review"  # /CASESX is not under /CASES


# --- reporting ---

def test_summarize_counts_and_bytes():
    recs = [rec("/PICTURES/2020/p.jpg", "S", size=100), rec("/PICTURES/dup/p.jpg", "S", size=100),
            rec("/HR/a.pdf", "T", size=5), rec("/HR/a (1).pdf", "T", size=5)]
    s = summarize(plan_dedup(recs, protect_prefixes=LEGAL))
    assert s["intra-area"]["delete"] == 1 and s["intra-area"]["bytes"] == 100
    assert s["same-folder"]["delete"] == 1 and s["same-folder"]["bytes"] == 5


# --- 0-byte safety: empty files share one sha1+size and must NOT be dedup candidates ---

def test_zero_byte_files_not_grouped_even_with_same_sha1():
    EMPTY = "da39a3ee5e6b4b0d3255bfef95601890afd80709"  # sha1 of the empty file
    recs = [rec("/a/x", EMPTY, size=0), rec("/b/y", EMPTY, size=0), rec("/c/z", EMPTY, size=0)]
    assert find_duplicate_groups(recs) == {}   # 0 bytes reclaimable; unrelated empties
    assert plan_dedup(recs) == []


def test_zero_byte_does_not_touch_real_same_size_duplicates():
    EMPTY = "da39a3ee5e6b4b0d3255bfef95601890afd80709"
    recs = [rec("/z/a", EMPTY, size=0), rec("/z/b", EMPTY, size=0),
            rec("/HR/r.pdf", "S", size=7), rec("/HR/r (1).pdf", "S", size=7)]
    groups = find_duplicate_groups(recs)
    assert set(groups) == {"S"}                 # only the real, non-empty duplicate survives


# --- missing-uid safety: a delete-by-uid executor must never auto-delete a uid-less record ---

def test_uidless_delete_candidate_goes_to_review_not_delete():
    keep = rec("/HR/a.pdf", "S", uid="UK")
    dup = rec("/HR/a (1).pdf", "S", uid="")     # no stable uid -> cannot be targeted safely
    [dec] = plan_dedup([keep, dup])
    assert dec.keep.uid == "UK"
    assert all(r.uid for r in dec.delete)        # nothing uid-less in delete
    assert {r.path for r in dec.review} == {"/HR/a (1).pdf"}


def test_uidless_candidate_moved_to_review_even_when_keep_has_no_uid():
    keep = rec("/HR/a.pdf", "S", uid="")
    dup = rec("/PICTURES/a.pdf", "S", uid="")
    decs = plan_dedup(keep and [keep, dup] or [])
    [dec] = decs
    assert all(r.uid for r in dec.delete)        # no uid-less record is ever an auto-delete


# --- protection is case-insensitive (never let folder casing defeat a legal protect prefix) ---

def test_protect_prefix_is_case_insensitive():
    recs = [rec("/Cases/Fall/doc.pdf", "S"), rec("/PICTURES/doc.pdf", "S")]
    [dec] = plan_dedup(recs, protect_prefixes=("/CASES",))
    assert dec.reason == "protect"
    assert dec.keep.path == "/Cases/Fall/doc.pdf"
    assert {r.path for r in dec.delete} == {"/PICTURES/doc.pdf"}


def test_protect_case_insensitive_sibling_boundary_preserved():
    # casefolding must not over-match: /casesx is still NOT under /CASES
    recs = [rec("/casesx/doc.pdf", "S"), rec("/PICTURES/doc.pdf", "S")]
    [dec] = plan_dedup(recs, protect_prefixes=("/CASES",))
    assert dec.reason == "review"
