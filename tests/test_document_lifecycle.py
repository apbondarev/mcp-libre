"""Tests for saving a document under a name, closing it and renaming it.

Three things were measured on a live LibreOffice and shape all of this:
storeAsURL with no FilterName writes ODF whatever the file is called, so a
document "saved as .docx" was ODF under a misleading name; close(True)
closes a modified document without a murmur and the changes are gone; and
UNO has no rename — storeAsURL leaves the old file exactly where it was.
"""

import asyncio
import os

import pytest

from tests.fake_writer import FakeDesktop, writer_doc
from tests.uno_stubs import install_uno_stubs

install_uno_stubs()

from uno_bridge import UNOBridge  # noqa: E402


@pytest.fixture
def bridge():
    return UNOBridge()


@pytest.fixture
def doc(tmp_path):
    document = writer_doc(["Heading", "Body text"], caret=(1, 0))
    document.url = f"file://{tmp_path / 'guide.odt'}"
    with open(tmp_path / "guide.odt", "wb") as handle:
        handle.write(b"PK\x03\x04 fake odf")
    return document


@pytest.fixture
def unsaved():
    document = writer_doc(["Heading", "Body text"], caret=(1, 0))
    document.url = ""
    return document


# --- saving ------------------------------------------------------------------

def test_saves_where_the_document_lives(bridge, doc):
    doc.modified = True

    saved = bridge.save_document(doc=doc)

    assert saved["success"] is True
    assert doc.stored_in_place == 1
    assert doc.isModified() is False


def test_a_document_that_lives_nowhere_needs_a_path(bridge, unsaved):
    refused = bridge.save_document(doc=unsaved)

    assert refused["success"] is False
    assert "never been saved" in refused["error"]
    assert "file_path" in refused["error"]


def test_saving_under_a_name_moves_the_document_there(bridge, unsaved, tmp_path):
    target = tmp_path / "new.odt"

    saved = bridge.save_document(doc=unsaved, file_path=str(target))

    assert saved["success"] is True
    assert saved["saved_as"] == str(target)
    assert saved["format"] == "odt"
    assert saved["filter"] == "writer8"
    assert os.path.exists(target)
    assert unsaved.getURL() == f"file://{target}"     # it lives there now


def test_the_extension_chooses_the_format(bridge, unsaved, tmp_path):
    saved = bridge.save_document(doc=unsaved,
                                 file_path=str(tmp_path / "for-word.docx"))

    assert saved["format"] == "docx"
    assert saved["filter"] == "MS Word 2007 XML"
    assert unsaved.stored_as[-1][1] == "MS Word 2007 XML"


def test_a_format_nobody_here_writes_is_refused(bridge, unsaved, tmp_path):
    refused = bridge.save_document(doc=unsaved,
                                   file_path=str(tmp_path / "thing.pages"))

    assert refused["success"] is False
    assert "pages" in refused["error"]
    assert "odt" in refused["error"]          # says what it can write
    assert not os.path.exists(tmp_path / "thing.pages")


def test_pdf_is_an_export_not_a_place_to_live(bridge, unsaved, tmp_path):
    refused = bridge.save_document(doc=unsaved,
                                   file_path=str(tmp_path / "guide.pdf"))

    assert refused["success"] is False
    assert "export_document" in refused["error"]


def test_a_name_with_no_extension_and_no_format_is_refused(bridge, unsaved,
                                                           tmp_path):
    refused = bridge.save_document(doc=unsaved, file_path=str(tmp_path / "guide"))

    assert refused["success"] is False
    assert "no extension" in refused["error"]


def test_the_format_can_be_said_outright(bridge, unsaved, tmp_path):
    saved = bridge.save_document(doc=unsaved, file_path=str(tmp_path / "guide"),
                                 document_format="odt")

    assert saved["format"] == "odt"


def test_a_file_that_is_already_there_is_not_written_over(bridge, unsaved,
                                                          tmp_path):
    existing = tmp_path / "taken.odt"
    existing.write_bytes(b"do not lose me")

    refused = bridge.save_document(doc=unsaved, file_path=str(existing))

    assert refused["success"] is False
    assert "overwrite=true" in refused["error"]
    assert existing.read_bytes() == b"do not lose me"

    allowed = bridge.save_document(doc=unsaved, file_path=str(existing),
                                   overwrite=True)
    assert allowed["success"] is True


# --- closing -----------------------------------------------------------------

def test_closes_a_document_with_nothing_unsaved(bridge, doc):
    closed = bridge.close_document(doc=doc)

    assert closed["success"] is True
    assert doc.closed is True
    assert closed["changes_saved"] is False
    assert closed["changes_discarded"] is False


def test_refuses_to_close_over_unsaved_changes(bridge, doc):
    doc.modified = True

    refused = bridge.close_document(doc=doc)

    assert refused["success"] is False
    assert refused["modified"] is True
    assert "unsaved=\"save\"" in refused["error"]
    assert "unsaved=\"discard\"" in refused["error"]
    assert getattr(doc, "closed", False) is False


def test_saves_the_changes_on_the_way_out_when_told_to(bridge, doc):
    doc.modified = True

    closed = bridge.close_document(doc=doc, unsaved="save")

    assert closed["success"] is True
    assert closed["changes_saved"] is True
    assert doc.stored_in_place == 1
    assert doc.closed is True


def test_lets_the_changes_go_when_told_to(bridge, doc):
    doc.modified = True

    closed = bridge.close_document(doc=doc, unsaved="discard")

    assert closed["changes_discarded"] is True
    assert getattr(doc, "stored_in_place", 0) == 0
    assert doc.closed is True


def test_changes_of_a_document_that_lives_nowhere_cannot_be_saved(bridge,
                                                                  unsaved):
    unsaved.modified = True

    refused = bridge.close_document(doc=unsaved, unsaved="save")

    assert refused["success"] is False
    assert "never been saved" in refused["error"]
    assert getattr(unsaved, "closed", False) is False


def test_a_document_that_will_not_save_is_left_open(bridge, doc):
    doc.modified = True
    doc.store_fails = True

    refused = bridge.close_document(doc=doc, unsaved="save")

    assert refused["success"] is False
    assert "left open" in refused["error"]
    assert getattr(doc, "closed", False) is False


def test_a_veto_is_reported_rather_than_swallowed(bridge, doc):
    doc.close_vetoed = True

    refused = bridge.close_document(doc=doc)

    assert refused["success"] is False
    assert "would not close" in refused["error"]


def test_an_unknown_answer_about_unsaved_changes_is_refused(bridge, doc):
    doc.modified = True

    refused = bridge.close_document(doc=doc, unsaved="maybe")

    assert refused["success"] is False
    assert '"save" or "discard"' in refused["error"]


def test_says_what_is_still_open(bridge, doc, tmp_path):
    other = writer_doc(["Another"], caret=(0, 0))
    other.url = f"file://{tmp_path / 'other.odt'}"
    bridge.desktop = FakeDesktop([doc, other])

    closed = bridge.close_document(doc=doc)

    assert closed["documents_still_open"] == [str(tmp_path / "other.odt")]
    assert closed["path"] == str(tmp_path / "guide.odt")


# --- renaming ----------------------------------------------------------------

def test_renaming_writes_the_new_name_and_keeps_the_old_file(bridge, doc,
                                                             tmp_path):
    renamed = bridge.rename_document("guide-v2.odt", doc=doc)

    assert renamed["success"] is True
    assert renamed["renamed_to"] == str(tmp_path / "guide-v2.odt")
    assert renamed["was"] == str(tmp_path / "guide.odt")
    assert renamed["original_kept"] is True
    assert "delete_original=true" in renamed["note"]
    assert os.path.exists(tmp_path / "guide-v2.odt")
    assert os.path.exists(tmp_path / "guide.odt")        # UNO has no rename
    assert doc.getURL() == f"file://{tmp_path / 'guide-v2.odt'}"


def test_the_old_file_goes_when_that_is_asked_for(bridge, doc, tmp_path):
    lock = tmp_path / ".~lock.guide.odt#"
    lock.write_text("stale")

    renamed = bridge.rename_document("guide-v2.odt", doc=doc,
                                     delete_original=True)

    assert renamed["original_kept"] is False
    assert not os.path.exists(tmp_path / "guide.odt")
    assert not os.path.exists(lock)          # and its stale lock with it


def test_a_bare_name_keeps_the_directory_and_the_extension(bridge, doc,
                                                           tmp_path):
    renamed = bridge.rename_document("Руководство", doc=doc)

    assert renamed["renamed_to"] == str(tmp_path / "Руководство.odt")


def test_renaming_to_another_extension_says_the_format_changed(bridge, doc,
                                                               tmp_path):
    renamed = bridge.rename_document("guide.docx", doc=doc)

    assert renamed["format"] == "docx"
    assert renamed["format_changed"] is True


def test_renaming_onto_an_existing_file_is_refused(bridge, doc, tmp_path):
    taken = tmp_path / "taken.odt"
    taken.write_bytes(b"do not lose me")

    refused = bridge.rename_document("taken.odt", doc=doc)

    assert refused["success"] is False
    assert "overwrite=true" in refused["error"]
    assert taken.read_bytes() == b"do not lose me"


def test_renaming_to_the_same_name_is_refused(bridge, doc):
    refused = bridge.rename_document("guide.odt", doc=doc)

    assert refused["success"] is False
    assert "already called" in refused["error"]


def test_a_document_that_lives_nowhere_has_no_name_to_change(bridge, unsaved):
    refused = bridge.rename_document("something.odt", doc=unsaved)

    assert refused["success"] is False
    assert "never been saved" in refused["error"]
    assert "save_document" in refused["error"]


def test_the_tools_are_registered_and_dispatch(tmp_path):
    from mcp_server import LibreOfficeMCPServer

    server = LibreOfficeMCPServer()
    doc = writer_doc(["Heading", "Body"], caret=(1, 0))
    doc.url = f"file://{tmp_path / 'a.odt'}"
    (tmp_path / "a.odt").write_bytes(b"PK\x03\x04")
    server.uno_bridge.desktop = FakeDesktop([doc])

    renamed = asyncio.run(server.execute_tool(
        "rename_document_live", {"new_name": "b.odt", "delete_original": True}))
    assert renamed["renamed_to"] == str(tmp_path / "b.odt")

    doc.modified = True
    refused = asyncio.run(server.execute_tool("close_document_live", {}))
    assert refused["success"] is False

    closed = asyncio.run(server.execute_tool("close_document_live",
                                             {"unsaved": "discard"}))
    assert closed["success"] is True
    assert doc.closed is True
