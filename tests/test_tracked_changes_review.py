"""Tests for reading the recorded changes, and for settling them.

`track_changes` on every mutating tool says whether an edit is recorded; this
is the other half, and without it the server could fill a document with
redlines and offer no way out but the user's own mouse.

What a dispatch does to the *text* is LibreOffice's business and is checked
in tests/live/writer_tools_check.py — measured there: three redlines to none,
an accepted insertion kept, a rejected deletion put back. What is checked
here is everything around it: which changes a selector picks, that it refuses
to guess, and that the bookkeeping matches.
"""

import pytest

from tests.fake_writer import FakeDesktop, writer_doc
from tests.uno_stubs import install_uno_stubs

install_uno_stubs()

from uno_bridge import UNOBridge  # noqa: E402

PARAGRAPHS = ["Введение", "query is the entry point", "mutation changes things"]
# (paragraph, start, end, kind, author)
CHANGES = [(1, 0, 5, "Insert", "Reviewer"),
           (1, 6, 8, "Delete", "Reviewer"),
           (2, 0, 8, "Format", "Клод")]


@pytest.fixture
def bridge():
    return UNOBridge()


@pytest.fixture
def doc():
    made = writer_doc(PARAGRAPHS, caret=(0, 0), redlines=CHANGES)
    made.RecordChanges = True
    return made


def test_every_change_comes_back_with_what_it_did_and_where(bridge, doc):
    listed = bridge.list_tracked_changes(doc=doc)

    assert listed["count"] == 3
    assert [one["kind"] for one in listed["changes"]] == ["insert", "delete",
                                                          "format"]
    assert listed["changes"][0]["text"] == "query"
    assert listed["changes"][0]["address"]["paragraph"] == 1
    assert listed["changes"][0]["date"]
    assert listed["recording"] is True
    assert listed["authors"] == ["Reviewer", "Клод"]


def test_a_change_says_what_writer_calls_it(bridge, doc):
    listed = bridge.list_tracked_changes(doc=doc)

    assert "query" in listed["changes"][0]["description"]


def test_changes_can_be_asked_for_by_author(bridge, doc):
    listed = bridge.list_tracked_changes(author="Клод", doc=doc)

    assert listed["count"] == 1
    assert listed["changes"][0]["kind"] == "format"


def test_changes_can_be_asked_for_by_place(bridge, doc):
    listed = bridge.list_tracked_changes({"paragraph": 2}, doc=doc)

    assert [one["author"] for one in listed["changes"]] == ["Клод"]


def test_a_document_that_records_nothing_still_reports_its_changes(bridge):
    quiet = writer_doc(PARAGRAPHS, caret=(0, 0), redlines=CHANGES[:1])

    listed = bridge.list_tracked_changes(doc=quiet)

    assert (listed["count"], listed["recording"]) == (1, False)


def test_accepting_one_takes_it_out_of_the_list(bridge, doc):
    first = bridge.list_tracked_changes(doc=doc)["changes"][0]

    settled = bridge.accept_tracked_changes(change_id=first["id"], doc=doc)

    assert settled["success"] is True
    assert (settled["settled"], settled["decision"]) == (1, "accept")
    assert settled["left"] == 2
    assert first["id"] not in [one["id"] for one
                               in bridge.list_tracked_changes(doc=doc)["changes"]]


def test_rejecting_by_author_settles_only_theirs(bridge, doc):
    settled = bridge.reject_tracked_changes(author="Reviewer", doc=doc)

    assert (settled["settled"], settled["decision"]) == (2, "reject")
    left = bridge.list_tracked_changes(doc=doc)
    assert [one["author"] for one in left["changes"]] == ["Клод"]


def test_all_goes_through_the_one_command(bridge, doc):
    from tests.uno_stubs import FakeComponentContext

    dispatcher = FakeComponentContext.ServiceManager.dispatcher
    dispatcher.sent.clear()

    settled = bridge.accept_tracked_changes(all=True, doc=doc)

    assert settled["settled"] == 3
    assert settled["one_by_one"] is False
    assert dispatcher.sent == [".uno:AcceptAllTrackedChanges"]
    assert bridge.list_tracked_changes(doc=doc)["count"] == 0


def test_settling_by_place_leaves_the_rest(bridge, doc):
    settled = bridge.accept_tracked_changes(address={"paragraph": 1}, doc=doc)

    assert settled["settled"] == 2
    assert bridge.list_tracked_changes(doc=doc)["count"] == 1


def test_it_refuses_to_guess_which_changes_are_meant(bridge, doc):
    nothing = bridge.accept_tracked_changes(doc=doc)
    both = bridge.accept_tracked_changes(author="Reviewer", all=True, doc=doc)

    for refused in (nothing, both):
        assert refused["success"] is False
        assert refused["code"] == "INVALID_PARAMETER"
    assert bridge.list_tracked_changes(doc=doc)["count"] == 3


def test_a_change_that_is_not_there(bridge, doc):
    refused = bridge.reject_tracked_changes(change_id="nosuch", doc=doc)

    assert (refused["success"], refused["code"]) == (False, "NOT_FOUND")


def test_a_selector_that_matches_nothing_settles_nothing(bridge, doc):
    settled = bridge.accept_tracked_changes(author="Nobody", doc=doc)

    assert (settled["success"], settled["settled"]) == (True, 0)
    assert bridge.list_tracked_changes(doc=doc)["count"] == 3


def test_the_readers_own_selection_is_put_back(bridge, doc):
    before = doc.getCurrentController().getSelection()

    bridge.accept_tracked_changes(all=True, doc=doc)

    assert doc.getCurrentController().getSelection() is before


def test_a_read_only_document_refuses(bridge, doc):
    doc.readonly = True

    refused = bridge.reject_tracked_changes(all=True, doc=doc)

    assert (refused["success"], refused["code"]) == (False, "READ_ONLY")


def test_settling_is_one_undo_step(bridge, doc):
    bridge.reject_tracked_changes(all=True, doc=doc)

    assert doc.UndoManager.calls[0] == ("enter", "MCP: reject tracked changes")


def test_settling_does_not_claim_to_have_been_recorded(bridge, doc):
    # _guarded_edit reports whether the edit was recorded; for a decision
    # about a recording that is not a fact about anything.
    settled = bridge.accept_tracked_changes(all=True, doc=doc)

    assert "tracked" not in settled


def test_runs_say_which_of_them_a_change_covers(bridge):
    """A recorded change marks its text with empty Redline portions, the way
    a comment does — measured. Runs that look ordinary are how a rewrite came
    to turn a struck-out deletion back into live text."""
    marked = writer_doc(["query is the entry point"], caret=(0, 0),
                        portions={0: [
                            {"kind": "Redline", "text": "", "redline": {
                                "id": "r1", "type": "Delete",
                                "author": "Reviewer"}, "is_start": True},
                            {"text": "query"},
                            {"kind": "Redline", "text": "", "redline": {
                                "id": "r1", "type": "Delete",
                                "author": "Reviewer"}, "is_start": False},
                            {"text": " is the entry point"}]})

    runs = bridge.read_runs({"paragraph": 0}, doc=marked)

    assert [run["text"] for run in runs["runs"]] == ["query",
                                                     " is the entry point"]
    assert [one["kind"] for one in runs["runs"][0]["changes"]] == ["delete"]
    assert runs["runs"][1]["changes"] == []


def test_a_rewrite_over_a_recorded_change_is_refused(bridge):
    marked = writer_doc(["query is the entry point"], caret=(0, 0),
                        portions={0: [
                            {"kind": "Redline", "text": "", "redline": {
                                "id": "r1", "type": "Delete",
                                "author": "Reviewer"}, "is_start": True},
                            {"text": "query"},
                            {"kind": "Redline", "text": "", "redline": {
                                "id": "r1", "type": "Delete",
                                "author": "Reviewer"}, "is_start": False},
                            {"text": " is the entry point"}]})
    runs = bridge.read_runs({"paragraph": 0}, doc=marked)["runs"]

    refused = bridge.replace_runs({"paragraph": 0},
                                  [dict(run, text=run["text"].upper())
                                   for run in runs], doc=marked)

    assert refused["success"] is False
    assert refused["code"] == "WOULD_LOSE_FORMATTING"
    assert "accept_tracked_changes" in refused["error"]
    assert [one["kind"] for one in refused["changes"]] == ["delete"]
    assert marked.getText().paragraphs[0] == "query is the entry point"


def test_flatten_writes_over_them_knowingly(bridge):
    marked = writer_doc(["query is the entry point"], caret=(0, 0),
                        portions={0: [
                            {"kind": "Redline", "text": "", "redline": {
                                "id": "r1", "type": "Delete",
                                "author": "Reviewer"}, "is_start": True},
                            {"text": "query"},
                            {"kind": "Redline", "text": "", "redline": {
                                "id": "r1", "type": "Delete",
                                "author": "Reviewer"}, "is_start": False},
                            {"text": " is the entry point"}]})
    runs = bridge.read_runs({"paragraph": 0}, doc=marked)["runs"]

    written = bridge.replace_runs({"paragraph": 0},
                                  [dict(run, text=run["text"].upper())
                                   for run in runs],
                                  flatten=True, doc=marked)

    assert written["success"] is True
