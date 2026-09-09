"""Tests for working with the pictures in a document.

A picture adds no characters to the text: Writer shows it in the portions as
an empty one of type "Frame" at its anchor offset, which is how reading runs
came to skip it and report nothing at all. Worse, replacing text that an
inline picture sits in destroys the picture — measured on a live LibreOffice,
one picture in, none out — while a picture anchored to a character survives
with its anchor moved to the start of the replaced stretch.
"""

import asyncio
import os

import pytest

from tests.fake_writer import FakeDesktop, FakeLocale, writer_doc
from tests.uno_stubs import install_uno_stubs

install_uno_stubs()

from uno_bridge import UNOBridge  # noqa: E402

EN = FakeLocale("en", "US")
BODY = "query is the entry point"
# The Frame portion is what splits the runs around a picture in Writer.
WITH_PICTURE = [
    {"text": "query ", "locale": EN},
    {"kind": "Frame", "text": ""},
    {"text": "is the entry point", "locale": EN},
]


@pytest.fixture
def bridge():
    return UNOBridge()


@pytest.fixture
def doc():
    return writer_doc(
        ["Schemas and Types", BODY], caret=(1, 0),
        selection_spans=[((1, 0), (1, 12))],
        styles=["Heading 2", "Standard"],
        outline_levels=[2, 0],
        portions={1: WITH_PICTURE},
        images=[{"name": "Image1", "paragraph": 1, "offset": 6,
                 "title": "GraphQL schema", "description": "схема запроса",
                 "width": 2434, "height": 2452}])


def test_says_that_the_text_holds_a_picture(bridge, doc):
    listed = bridge.list_images(doc=doc)

    assert listed["success"] is True
    assert listed["count"] == 1
    image, = listed["images"]
    assert image["name"] == "Image1"
    assert image["inline"] is True
    assert image["anchor"] == "AS_CHARACTER"


def test_says_where_the_picture_is_and_what_it_is_anchored_to(bridge, doc):
    image, = bridge.list_images(doc=doc)["images"]

    assert image["address"] == {"paragraph": 1, "offset": 6, "length": 0}
    assert image["paragraph_text"] == BODY
    assert image["width_mm"] == 24.3
    assert image["height_mm"] == 24.5
    assert image["pixels"] == {"width": 8, "height": 8}
    assert image["mime_type"] == "image/png"
    assert image["linked"] is False


def test_reports_the_alternative_text_a_reader_would_hear(bridge, doc):
    image, = bridge.list_images(doc=doc)["images"]

    assert image["title"] == "GraphQL schema"
    assert image["description"] == "схема запроса"


def test_lists_the_pictures_by_scope(bridge, doc):
    assert bridge.list_images({"paragraph": 1}, doc=doc)["count"] == 1
    assert bridge.list_images({"paragraph": 0}, doc=doc)["count"] == 0
    assert bridge.list_images({"paragraph": 1, "offset": 0, "length": 6},
                              doc=doc)["count"] == 1
    assert bridge.list_images({"paragraph": 1, "offset": 8, "length": 6},
                              doc=doc)["count"] == 0
    # the selection covers offsets 0-12, so it holds the picture at 6
    assert bridge.list_images({"selection": True}, doc=doc)["count"] == 1


def test_the_runs_of_a_paragraph_carry_its_pictures(bridge, doc):
    """The picture travels on the run that *starts* at its offset, because
    that is the run whose rewrite destroys it: measured on a live
    LibreOffice, replacing the stretch that begins at the anchor kills the
    picture, while replacing the stretch that ends there leaves it."""
    runs = bridge.read_runs({"paragraph": 1}, doc=doc)["runs"]

    assert [run["text"] for run in runs] == ["query ", "is the entry point"]
    assert [len(run["images"]) for run in runs] == [0, 1]
    assert runs[1]["images"][0]["name"] == "Image1"
    # and the picture costs no characters
    assert sum(run["length"] for run in runs) == len(BODY)


def test_a_flat_replacement_that_would_destroy_it_is_refused(bridge, doc):
    refused = bridge.replace_range({"paragraph": 1}, "перевод", doc=doc)

    assert refused["success"] is False
    assert "inline picture" in refused["error"]
    assert "destroyed outright" in refused["error"]
    assert bridge.list_images(doc=doc)["count"] == 1


def test_flatten_true_goes_ahead_and_says_what_it_cost(bridge, doc):
    flattened = bridge.replace_range({"paragraph": 1}, "перевод", flatten=True,
                                     doc=doc)

    assert flattened["success"] is True
    assert flattened["images_dropped"] == 1


def test_rewriting_the_run_a_picture_sits_in_is_refused(bridge, doc):
    runs = bridge.read_runs({"paragraph": 1}, doc=doc)["runs"]
    translated = [dict(run, text="— точка входа") if run["images"]
                  else dict(run) for run in runs]

    refused = bridge.replace_runs({"paragraph": 1}, translated, doc=doc)

    assert refused["success"] is False
    assert "Image1" in refused["error"]
    assert "unchanged" in refused["error"]
    assert bridge.list_images(doc=doc)["count"] == 1


def test_leaving_that_run_alone_keeps_the_picture(bridge, doc):
    runs = bridge.read_runs({"paragraph": 1}, doc=doc)["runs"]
    translated = [dict(run) if run["images"]
                  else dict(run, text="запрос ") for run in runs]

    written = bridge.replace_runs({"paragraph": 1}, translated, doc=doc)

    assert written["success"] is True
    assert written["images_kept"] == 1
    assert written["runs_kept"] == 1
    assert bridge.list_images(doc=doc)["count"] == 1
    assert bridge.read_paragraphs(start=1, count=1,
                                  doc=doc)["paragraphs"][0]["text"] \
        == "запрос is the entry point"


def test_a_picture_anchored_to_a_character_is_not_inline(bridge):
    doc = writer_doc(["Heading", BODY], caret=(1, 0),
                     images=[{"name": "Anchored", "paragraph": 1, "offset": 6,
                              "inline": False}])

    image, = bridge.list_images(doc=doc)["images"]
    assert image["inline"] is False
    assert image["anchor"] == "AT_CHARACTER"
    # it survives a rewrite, so it is no reason to refuse one
    assert bridge.replace_range({"paragraph": 1}, "перевод",
                                doc=doc)["success"] is True


def test_writes_the_picture_to_a_file(bridge, doc, tmp_path):
    target = tmp_path / "schema.png"

    written = bridge.export_image("Image1", path=str(target), doc=doc)

    assert written["success"] is True
    assert written["path"] == str(target)
    assert os.path.exists(target)
    assert written["bytes"] == os.path.getsize(target)
    assert written["mime_type"] == "image/png"
    assert written["pixels"] == {"width": 8, "height": 8}
    assert written["address"] == {"paragraph": 1, "offset": 6, "length": 0}
    with open(target, "rb") as handle:
        assert handle.read(8) == b"\x89PNG\r\n\x1a\n"


def test_hands_the_picture_back_when_asked(bridge, doc, tmp_path):
    written = bridge.export_image("Image1", path=str(tmp_path / "a.png"),
                                  inline=True, doc=doc)

    assert written["inline"] is True
    assert written["_image_content"]["mime_type"] == "image/png"
    assert written["_image_content"]["data"].startswith("iVBORw0KGgo")


def test_a_picture_that_is_not_there_is_refused(bridge, doc):
    refused = bridge.export_image("Image9", doc=doc)

    assert refused["success"] is False
    assert "Image9" in refused["error"]
    assert "Image1" in refused["error"]      # says what the document holds


def test_a_format_it_cannot_write_is_refused(bridge, doc, tmp_path):
    refused = bridge.export_image("Image1", path=str(tmp_path / "a.xcf"),
                                  image_format="xcf", doc=doc)

    assert refused["success"] is False
    assert "format" in refused["error"]


def test_a_directory_that_is_not_there_is_refused(bridge, doc):
    refused = bridge.export_image("Image1", path="/nowhere/at/all/a.png",
                                  doc=doc)

    assert refused["success"] is False
    assert "/nowhere/at/all" in refused["error"]


def test_the_default_path_is_named_after_the_picture(bridge, doc):
    written = bridge.export_image("Image1", doc=doc)

    assert written["success"] is True
    assert written["path"].endswith("Image1.png")
    os.unlink(written["path"])


def test_the_picture_tools_are_registered_and_dispatch(tmp_path):
    from mcp_server import LibreOfficeMCPServer

    server = LibreOfficeMCPServer()
    doc = writer_doc(["Heading", BODY], caret=(1, 0), portions={1: WITH_PICTURE},
                     images=[{"name": "Image1", "paragraph": 1, "offset": 6}])
    server.uno_bridge.desktop = FakeDesktop([doc])

    listed = asyncio.run(server.execute_tool("list_images_live",
                                             {"address": {"paragraph": 1}}))
    assert listed["count"] == 1

    written = asyncio.run(server.execute_tool(
        "export_image_live", {"name": "Image1",
                              "path": str(tmp_path / "out.png")}))
    assert written["success"] is True
    assert os.path.exists(tmp_path / "out.png")


# Selecting a picture in Writer makes the selection the picture itself — an
# SwXTextGraphicObject with a name and no getCount — so every tool that asked
# it for a text range failed with "the selection is not a text range:
# getCount". A caller was left unable to say which of two pictures was in
# front of it, and had to ask.

@pytest.fixture
def picture_selected():
    return writer_doc(
        ["Introduction to GraphQL", BODY], caret=(1, 0),
        portions={1: WITH_PICTURE},
        images=[{"name": "Image1", "paragraph": 0, "offset": 0,
                 "inline": False},
                {"name": "Image2", "paragraph": 1, "offset": 6,
                 "title": "computer", "description": "иконка компьютера"}],
        selected_image="Image2")


def test_says_which_picture_is_selected(bridge, picture_selected):
    listed = bridge.list_images({"selection": True}, doc=picture_selected)

    assert listed["success"] is True
    assert listed["count"] == 1
    assert listed["images"][0]["name"] == "Image2"
    assert listed["images"][0]["description"] == "иконка компьютера"
    assert listed["scope"] == {"selection": "picture"}


def test_writes_the_selected_picture_without_being_told_its_name(
        bridge, picture_selected, tmp_path):
    written = bridge.export_image(path=str(tmp_path / "selected.png"),
                                  doc=picture_selected)

    assert written["success"] is True
    assert written["name"] == "Image2"
    assert written["was_selected"] is True
    assert os.path.exists(written["path"])


def test_a_named_picture_still_wins_over_the_selection(bridge, picture_selected,
                                                       tmp_path):
    written = bridge.export_image("Image1", path=str(tmp_path / "one.png"),
                                  doc=picture_selected)

    assert written["name"] == "Image1"
    assert written["was_selected"] is False


def test_no_name_and_no_selection_is_refused_with_the_names(bridge, doc):
    refused = bridge.export_image(doc=doc)

    assert refused["success"] is False
    assert "No picture is selected" in refused["error"]
    assert "Image1" in refused["error"]        # says what there is to choose


def test_the_cursor_report_says_a_picture_is_selected(bridge, picture_selected):
    info = bridge.get_cursor_info(doc=picture_selected)

    assert info["success"] is True
    assert info["selection_kind"] == "picture"
    assert [image["name"] for image in info["images"]] == ["Image2"]
    assert info["selected_text"] is None
    assert "export_image" in info["note"]


def test_a_text_tool_says_a_picture_is_selected_rather_than_getCount(
        bridge, picture_selected):
    refused = bridge.replace_selection("перевод", doc=picture_selected)

    assert refused["success"] is False
    assert "a picture is selected" in refused["error"]
    assert "Image2" in refused["error"]
    assert "export_image" in refused["error"]


def test_the_tool_takes_no_name_at_all(tmp_path):
    from mcp_server import LibreOfficeMCPServer

    server = LibreOfficeMCPServer()
    doc = writer_doc(["Heading", BODY], caret=(1, 0), portions={1: WITH_PICTURE},
                     images=[{"name": "Image2", "paragraph": 1, "offset": 6}],
                     selected_image="Image2")
    server.uno_bridge.desktop = FakeDesktop([doc])

    assert "name" not in server.tools["export_image_live"]["parameters"].get(
        "required", [])
    written = asyncio.run(server.execute_tool(
        "export_image_live", {"path": str(tmp_path / "sel.png")}))
    assert written["name"] == "Image2"
    assert written["was_selected"] is True
