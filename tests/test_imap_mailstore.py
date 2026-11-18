import re

import pytest

from nucleon.mailstore.imap import ImapMailStore

HDR = (
    b"Subject: Hello\r\nFrom: Al <al@x.com>\r\nTo: b@y.com, C <c@z.com>\r\n"
    b"Date: Mon, 03 Nov 2026 10:00:00 +0000\r\nMessage-ID: <m1@x.com>\r\n"
    b"In-Reply-To: <m0@x.com>\r\nReferences: <m-1@x.com> <m0@x.com>\r\n\r\n"
)
FULL = HDR + b"body line\r\n"


class FakeConn:
    def __init__(self):
        self.cmds = []  # tuples of (verb, args...)

    def select(self, mailbox, readonly=False):
        self.cmds.append(("SELECT", mailbox))
        return "OK", [b"3"]

    def response(self, code):
        return code, [b"77"]

    def uid(self, command, *args):
        self.cmds.append(("UID", command) + args)
        if command == "SEARCH":
            return "OK", [b"5 6 7"]
        if command == "FETCH":
            item = args[1]
            if "BODYSTRUCTURE" in item:
                line = (b'1 (UID %s RFC822.SIZE %d BODYSTRUCTURE ("text" "plain" '
                        b'("charset" "utf-8") NIL NIL "7bit" 11 1))' % (args[0].encode(), len(FULL)))
                return "OK", [line]
            if item.startswith("(BODY.PEEK[1]"):
                return "OK", [(b"1 (UID %s BODY[1] {11}" % args[0].encode(), b"body line\r\n"), b")"]
            data = HDR
            return "OK", [(b"1 (UID %s BODY[] {%d}" % (args[0].encode(), len(data)), data), b")"]
        return "OK", [b""]

    LIST = [
        b'(\\HasNoChildren) "/" "INBOX"',
        b'(\\HasNoChildren \\Trash) "/" "Papierkorb"',
        b'(\\HasNoChildren \\Sent) "/" "Gesendet"',
        b'(\\HasNoChildren \\Drafts) "/" "Entwuerfe"',
        b'(\\HasNoChildren \\Junk) "/" "Spam"',
        b'(\\HasNoChildren \\Archive) "/" "Archiv"',
        b'(\\HasNoChildren \\All) "/" "Alle"',
        b'(\\HasNoChildren \\Flagged) "/" "Markiert"',
        b'(\\Noselect \\HasChildren) "/" "Container"',
        b'(\\HasNoChildren) "/" "Labels/Work"',
        b'(\\HasNoChildren) "/" "nucleon/proposed"',
    ]
    list_status = "OK"

    def list(self, directory='""', pattern="*"):
        self.cmds.append(("LIST", directory, pattern))
        return self.list_status, self.LIST

    def append(self, mailbox, flags, date_time, message):
        self.cmds.append(("APPEND", mailbox, flags, date_time, message))
        return "OK", [b"[APPENDUID 77 42] done"]


@pytest.fixture
def conn():
    return FakeConn()


@pytest.fixture
def store(conn):
    return ImapMailStore(conn, "acct")


def _ref(store):
    return store.list_window("INBOX", 7, 10)[0]


def test_list_window_searches_since_and_limits(store, conn):
    refs = store.list_window("INBOX", 7, 2)
    assert [r.uid for r in refs] == [6, 7]  # newest subset
    assert refs[0].uidvalidity == 77 and refs[0].mailbox == "INBOX"
    search = [c for c in conn.cmds if c[:2] == ("UID", "SEARCH")][0]
    assert search[2] is None and search[3] == "SINCE"
    assert re.fullmatch(r"\d{2}-[A-Z][a-z]{2}-\d{4}", search[4])


def test_fetch_body_uses_peek(store, conn):
    ref = _ref(store)
    conn.cmds.clear()
    assert "body line" in store.fetch_body_text(ref)
    items = [c[3] for c in conn.cmds if c[:2] == ("UID", "FETCH")]
    # RFC822.SIZE/BODYSTRUCTURE are pure metadata and never set \\Seen.
    assert items and all("BODY.PEEK[" in i or i == "(RFC822.SIZE BODYSTRUCTURE)" for i in items)
    assert all(not re.search(r"(?<!PEEK)BODY\[", i) for i in items)


def test_fetch_meta_populates_message_id(store, conn):
    ref = _ref(store)
    conn.cmds.clear()
    m = store.fetch_meta(ref)
    assert m.message_id == "<m1@x.com>"
    assert m.subject == "Hello"
    assert "al@x.com" in m.from_addr
    assert m.to_addrs == ["b@y.com", "c@z.com"]
    assert m.in_reply_to == "<m0@x.com>"
    assert m.references == ["<m-1@x.com>", "<m0@x.com>"]
    assert m.date is not None
    items = [c[3] for c in conn.cmds if c[:2] == ("UID", "FETCH")]
    # RFC822.SIZE/BODYSTRUCTURE are pure metadata and never set \\Seen.
    assert items and all("BODY.PEEK[" in i or i == "(RFC822.SIZE BODYSTRUCTURE)" for i in items)


def test_list_sent_is_read_only(store, conn):
    metas = store.list_sent(30, 2)
    assert len(metas) == 2
    fetched = [c[2] for c in conn.cmds if c[:2] == ("UID", "FETCH")]
    assert fetched[0] == "6" and fetched[-1] == "7"
    assert ("SELECT", "Sent") in conn.cmds
    verbs = {c[1] for c in conn.cmds if c[0] == "UID"}
    assert verbs <= {"SEARCH", "FETCH"}
    assert not any(c[0] == "APPEND" for c in conn.cmds)


@pytest.mark.parametrize("target,folder", [("drafts", "Drafts"), ("outbox", "Nucleon/Outbox")])
def test_append_draft_sets_draft_flag(store, conn, target, folder):
    ref = store.append_draft(target, FULL)
    app = [c for c in conn.cmds if c[0] == "APPEND"][0]
    assert app[1] == folder
    assert "\\Draft" in app[2]
    assert app[4] == FULL
    assert ref.mailbox == folder and ref.uid == 42 and ref.uidvalidity == 77


def test_append_draft_rejects_unknown_target(store):
    with pytest.raises(ValueError):
        store.append_draft("inbox", FULL)


def test_move_to_trash_is_targeted(store, conn):
    ref = _ref(store)
    conn.cmds.clear()
    store.move_to_trash(ref)
    assert ("UID", "MOVE", str(ref.uid), "Trash") in conn.cmds
    assert ("SELECT", "INBOX") in conn.cmds


def test_no_untargeted_expunge_or_purge(store, conn):
    ref = _ref(store)
    store.move_to_trash(ref)
    store.set_label(ref, "Label")
    store.append_draft("drafts", FULL)
    store.list_sent(30, 5)
    for c in conn.cmds:
        assert c[0] != "EXPUNGE"
        if c[0] == "UID" and c[1] == "EXPUNGE":
            assert c[2] == str(ref.uid)
        if c[0] == "UID" and c[1] == "STORE":
            assert c[2] == str(ref.uid)
        assert not (c[0] == "SELECT" and c[1] == "Trash")
        assert c[1:2] != ("CLOSE",)


def test_set_label_copies_to_label_folder(store, conn):
    ref = _ref(store)
    conn.cmds.clear()
    store.set_label(ref, "Labels/Work")
    assert ("UID", "COPY", str(ref.uid), "Labels/Work") in conn.cmds
    assert not any(c[:2] == ("UID", "STORE") for c in conn.cmds)


# -- R-001: system/special folders are excluded via provider metadata ---------
def test_system_folders_from_list_flags(store):
    sf = {n.casefold() for n in store.system_folders()}
    for name in ("papierkorb", "gesendet", "entwuerfe", "spam", "archiv", "alle",
                 "markiert", "container", "inbox", "trash", "drafts", "sent"):
        assert name in sf
    assert "labels/work" not in sf and "nucleon/proposed" not in sf


def test_set_label_refuses_system_folder(store, conn):
    ref = _ref(store)
    conn.cmds.clear()
    for bad in ("Papierkorb", "papierkorb", "Archiv", "Trash", "INBOX"):
        with pytest.raises(ValueError):
            store.set_label(ref, bad)
    assert not any(c[:2] == ("UID", "COPY") for c in conn.cmds)


def test_set_label_fails_closed_when_list_fails(store, conn):
    conn.list_status = "NO"
    ref = _ref(store)
    with pytest.raises(RuntimeError):
        store.set_label(ref, "Labels/Work")
    assert not any(c[:2] == ("UID", "COPY") for c in conn.cmds)


# -- R-002: UIDVALIDITY is verified before every ref-based access -------------
class ChangedValidityConn(FakeConn):
    def response(self, code):
        return code, [b"99"]  # server now reports a different UIDVALIDITY


def _stale(store):
    ref = _ref(store)  # uidvalidity 77
    store._conn = ChangedValidityConn()
    store._conn.cmds = []
    return ref, store._conn


@pytest.mark.parametrize("op", ["fetch_meta", "fetch_body_text", "move_to_trash", "label"])
def test_changed_uidvalidity_aborts_without_touching_uid(store, op):
    from nucleon.mailstore import StaleReferenceError

    ref, c = _stale(store)
    call = {
        "fetch_meta": lambda: store.fetch_meta(ref),
        "fetch_body_text": lambda: store.fetch_body_text(ref),
        "move_to_trash": lambda: store.move_to_trash(ref),
        "label": lambda: store.set_label(ref, "Labels/Work"),
    }[op]
    with pytest.raises(StaleReferenceError):
        call()
    assert ("SELECT", "INBOX") in c.cmds  # re-selected before the check
    assert not any(x[0] == "UID" for x in c.cmds)


def test_unknown_uidvalidity_aborts(store):
    from nucleon.mailstore import StaleReferenceError

    ref = _ref(store)

    class NoValidity(FakeConn):
        def response(self, code):
            return code, [None]

    store._conn = NoValidity()
    with pytest.raises(StaleReferenceError):
        store.fetch_meta(ref)


def test_matching_uidvalidity_still_works(store, conn):
    ref = _ref(store)
    store.move_to_trash(ref)
    assert ("UID", "MOVE", str(ref.uid), "Trash") in conn.cmds


# -- R-005: stable paging; the limit is NOT applied inside the adapter --------
def test_iter_window_is_oldest_first_and_unlimited(store):
    assert [r.uid for r in store.iter_window("INBOX", 7)] == [5, 6, 7]


def test_iter_window_fetches_headers_lazily(store, conn):
    it = store.iter_window("INBOX", 7)
    conn.cmds.clear()
    first = next(it)
    assert first.uid == 5
    fetched = [c[2] for c in conn.cmds if c[:2] == ("UID", "FETCH")]
    assert fetched == ["5"]  # later UIDs untouched until the engine asks for them


def test_iter_window_stops_if_uidvalidity_changes_midway(store, conn):
    it = store.iter_window("INBOX", 7)
    assert next(it).uid == 5
    store._conn = ChangedValidityConn()
    assert list(it) == []


# -- R-004: read-only draft lookup --------------------------------------------
def test_find_draft_is_read_only_search_by_message_id(store, conn):
    refs = store.find_draft("drafts", "<nucleon-x@nucleon.invalid>")
    assert [r.uid for r in refs] == [5, 6, 7] and refs[0].mailbox == "Drafts"
    assert ("SELECT", "Drafts") in conn.cmds
    search = [c for c in conn.cmds if c[:2] == ("UID", "SEARCH")][0]
    assert search[3:] == ("HEADER", "Message-ID", "<nucleon-x@nucleon.invalid>")
    assert {c[1] for c in conn.cmds if c[0] == "UID"} <= {"SEARCH", "FETCH"}
    assert not any(c[0] == "APPEND" for c in conn.cmds)


def test_find_draft_rejects_unknown_target(store):
    with pytest.raises(ValueError):
        store.find_draft("inbox", "<a@b>")


def test_append_without_appenduid_is_reported_unconfirmed(conn):
    class NoUid(FakeConn):
        def append(self, *a):
            return "OK", [b"done"]

    ref = ImapMailStore(NoUid(), "acct").append_draft("drafts", FULL)
    assert (ref.uidvalidity, ref.uid) == (0, 0)
