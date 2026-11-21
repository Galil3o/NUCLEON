from proton_drive_kit.dedup import find_duplicate_groups, plan_dedup, summarize
from proton_drive_kit.records import DriveRecord

LEGAL = ("/CASES",)


def rec(path, sha1, size=10):
    return DriveRecord(
        uid=path, parent_uid=None, name=path.rstrip("/").rsplit("/", 1)[-1], path=path,
        is_folder=False, sha1=sha1, sha1_verified=False, size=size, mtime=None,
        media_type=None, shared_by_url=False)


def folder(path):
    return DriveRecord(
        uid=path, parent_uid=None, name=path.rstrip("/").rsplit("/", 1)[-1], path=path,
        is_folder=True, sha1=None, sha1_verified=False, size=None, mtime=None,
        media_type=None, shared_by_url=False)


def test_find_groups_ignores_folders_singletons_and_no_sha1():
    recs = [rec("/a/x.pdf", "S"), rec("/b/y.pdf", "S"), rec("/c/z.pdf", "T"),
            folder("/d"), rec("/e/n.pdf", "")]
    groups = find_duplicate_groups(recs)
    assert set(groups) == {"S"}
    assert {r.path for r in groups["S"]} == {"/a/x.pdf", "/b/y.pdf"}


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


def test_clear_copy_deletes_archive_keeps_original():
    recs = [rec("/COMPANY/proj/report.pdf", "S"), rec("/COMPANY/0 ARCHIVE/report.pdf", "S")]
    [dec] = plan_dedup(recs)
    assert dec.reason == "clear-copy"
    assert dec.keep.path == "/COMPANY/proj/report.pdf"
    assert {r.path for r in dec.delete} == {"/COMPANY/0 ARCHIVE/report.pdf"}


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


def test_review_group_still_deletes_same_folder_extras():
    recs = [rec("/CASES/A/doc.pdf", "S"), rec("/CASES/A/doc (1).pdf", "S"),
            rec("/CASES/B/doc.pdf", "S")]
    [dec] = plan_dedup(recs, protect_prefixes=LEGAL)
    assert dec.reason == "review"
    assert {r.path for r in dec.delete} == {"/CASES/A/doc (1).pdf"}
    assert {r.path for r in dec.review} == {"/CASES/B/doc.pdf"}


def test_summarize_counts_and_bytes():
    recs = [rec("/PICTURES/2020/p.jpg", "S", size=100), rec("/PICTURES/dup/p.jpg", "S", size=100),
            rec("/HR/a.pdf", "T", size=5), rec("/HR/a (1).pdf", "T", size=5)]
    s = summarize(plan_dedup(recs, protect_prefixes=LEGAL))
    assert s["intra-area"]["delete"] == 1 and s["intra-area"]["bytes"] == 100
    assert s["same-folder"]["delete"] == 1 and s["same-folder"]["bytes"] == 5
