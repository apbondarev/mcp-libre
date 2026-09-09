"""Tests for rendering a page as a picture, with LibreOffice alone.

Measured on a live LibreOffice: the filter "writer_png_Export" writes one
page at the pixel size in its FilterData, and it renders the page the *view*
is on — the page properties in the FilterData are ignored. So a page is
rendered by jumping the view cursor there and putting it back. When that
filter will not do it, the page goes out as PDF and comes back through a
hidden Draw document. Neither path uses anything outside LibreOffice, which
is a requirement on every tool of this server.
"""

import asyncio
import os

import pytest

from tests.fake_writer import FakeDesktop, writer_doc
from tests.uno_stubs import install_uno_stubs

install_uno_stubs()

from uno_bridge import UNOBridge  # noqa: E402

PARAGRAPHS = ["Getting started with GraphQL", "In this tutorial-style intro",
              "Introduction to GraphQL", "Learn about GraphQL", "Schemas",
              "The type system"]


@pytest.fixture
def bridge():
    return UNOBridge()


@pytest.fixture
def doc():
    return writer_doc(PARAGRAPHS, caret=(1, 0), pages=3)


def test_renders_the_page_the_reader_is_looking_at(bridge, doc, tmp_path):
    doc.getCurrentController().getViewCursor().page = 2

    rendered = bridge.render_page(path=str(tmp_path / "page.png"), inline=False,
                                  doc=doc)

    assert rendered["success"] is True
    assert rendered["page"] == 2
    assert rendered["pages"] == 3
    assert rendered["rendered_by"] == "writer_png_Export"
    assert os.path.exists(rendered["path"])
    assert rendered["bytes"] == os.path.getsize(rendered["path"])


def test_renders_the_page_asked_for(bridge, doc, tmp_path):
    rendered = bridge.render_page(page=3, path=str(tmp_path / "p3.png"),
                                  inline=False, doc=doc)

    assert rendered["page"] == 3
    assert doc.getCurrentController().getViewCursor().jumps == [3]
    # and the reader's cursor is where it was
    assert doc.getCurrentController().getViewCursor().start == (1, 0)


def test_renders_the_page_a_paragraph_is_on(bridge, doc, tmp_path):
    rendered = bridge.render_page(address={"paragraph": 4},
                                  path=str(tmp_path / "a.png"), inline=False,
                                  doc=doc)

    assert rendered["success"] is True
    assert rendered["page"] == 3          # the fake puts ¶4 on page 3
    assert doc.getCurrentController().getViewCursor().start == (1, 0)


def test_the_size_comes_from_the_page_and_the_resolution(bridge, doc, tmp_path):
    rendered = bridge.render_page(page=1, dpi=110, path=str(tmp_path / "a.png"),
                                  inline=False, doc=doc)

    # A4 at 110 dpi: 210 mm and 297 mm across
    assert rendered["pixels"] == {"width": 909, "height": 1286}
    assert rendered["dpi"] == 110
    filter_name, _path, settings = doc.stored[-1]
    assert filter_name == "writer_png_Export"
    assert settings["FilterName"] == "writer_png_Export"


def test_a_page_the_document_does_not_have_is_refused(bridge, doc):
    refused = bridge.render_page(page=9, doc=doc)

    assert refused["success"] is False
    assert "3 pages" in refused["error"]


def test_a_resolution_out_of_range_is_refused(bridge, doc):
    for dpi in (0, 19, 301, 2000, "big"):
        refused = bridge.render_page(page=1, dpi=dpi, doc=doc)
        assert refused["success"] is False
        assert "dpi" in refused["error"]


def test_a_page_number_that_is_not_one_is_refused(bridge, doc):
    refused = bridge.render_page(page=0, doc=doc)

    assert refused["success"] is False
    assert "page must be" in refused["error"]


def test_a_directory_that_is_not_there_is_refused(bridge, doc):
    refused = bridge.render_page(page=1, path="/nowhere/at/all/page.png",
                                 doc=doc)

    assert refused["success"] is False
    assert "/nowhere/at/all" in refused["error"]


def test_hands_the_page_back_to_be_looked_at(bridge, doc, tmp_path):
    rendered = bridge.render_page(page=1, path=str(tmp_path / "a.png"),
                                  doc=doc)

    assert rendered["inline"] is True
    assert rendered["_image_content"]["mime_type"] == "image/png"
    assert rendered["_image_content"]["data"].startswith("iVBORw0KGgo")


def test_falls_back_to_the_pdf_route_when_the_filter_will_not(bridge, doc,
                                                              tmp_path):
    doc.render_failures = ("writer_png_Export",)
    desktop = FakeDesktop([doc])
    bridge.desktop = desktop

    rendered = bridge.render_page(page=2, path=str(tmp_path / "a.png"),
                                  inline=False, doc=doc)

    assert desktop.loaded[-1][1] == "draw_pdf_import"
    assert desktop.drawings[-1].closed is True
    assert desktop.drawings[-1].stored[-1][0] == "draw_png_Export"

    assert rendered["success"] is True
    assert rendered["rendered_by"] == "PDF through a hidden Draw document"
    assert [name for name, _path, _settings in doc.stored] \
        == ["writer_pdf_Export"]
    # the PDF asked for that page, which the PDF filter does honour
    _name, _path, settings = doc.stored[-1]
    assert settings["FilterName"] == "writer_pdf_Export"


def test_says_so_when_neither_route_works(bridge, doc):
    doc.render_failures = ("writer_png_Export", "writer_pdf_Export")

    refused = bridge.render_page(page=2, doc=doc)

    assert refused["success"] is False
    assert "neither" in refused["error"]


def test_it_says_what_the_picture_does_not_show(bridge, doc, tmp_path):
    rendered = bridge.render_page(page=1, path=str(tmp_path / "a.png"),
                                  inline=False, doc=doc)

    assert "as it prints" in rendered["shows"]
    assert "spell checker" in rendered["shows"]


def test_the_tool_is_registered_and_dispatches(tmp_path):
    from mcp_server import LibreOfficeMCPServer

    server = LibreOfficeMCPServer()
    doc = writer_doc(PARAGRAPHS, caret=(1, 0), pages=2)
    server.uno_bridge.desktop = FakeDesktop([doc])

    rendered = asyncio.run(server.execute_tool(
        "render_page_live", {"page": 2, "path": str(tmp_path / "p.png"),
                             "inline": False}))

    assert rendered["success"] is True
    assert rendered["page"] == 2
    assert os.path.exists(tmp_path / "p.png")
