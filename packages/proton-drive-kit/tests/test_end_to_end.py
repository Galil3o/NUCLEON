import proton_drive_kit as pdk


def _frec(path, sha1):
    return pdk.DriveRecord(uid="u", parent_uid=None, name=path.rsplit("/", 1)[-1], path=path,
                           is_folder=False, sha1=sha1, sha1_verified=False, size=1, mtime=None,
                           media_type=None, shared_by_url=False)


def _drec(path):
    return pdk.DriveRecord(uid="u", parent_uid=None, name=path.rsplit("/", 1)[-1], path=path,
                           is_folder=True, sha1=None, sha1_verified=False, size=None, mtime=None,
                           media_type=None, shared_by_url=False)


class _Acc:
    def __init__(self, tree):
        self.tree = tree

    def list_dir(self, p):
        return list(self.tree.get(p, []))


def test_public_api_exports():
    for n in ("DriveRecord", "parse_entry", "parse_listing", "DataMap", "DriveAccess", "scan"):
        assert hasattr(pdk, n), n


def test_scan_builds_datamap_and_finds_attachment():
    acc = _Acc({"/r": [_drec("/r/case")], "/r/case": [_frec("/r/case/doc.pdf", "SHA")]})
    m = pdk.DataMap()
    m.add_all(pdk.scan(acc, "/r"))
    assert m.contains_sha1("SHA") == ["/r/case/doc.pdf"]
    assert m.contains_sha1("absent") == []
    assert m.contains_sha1("SHA", under="/r/case") == ["/r/case/doc.pdf"]
