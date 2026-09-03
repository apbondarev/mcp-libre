"""Tests for marking text with a language.

Replacing English with a translation kept the range's en-US locale, so every
Russian word came back underlined as a spelling error. The language has to
travel with the replacement, and has to be fixable after the fact.
"""

import asyncio

import pytest

from tests.fake_writer import FakeCalcDoc, FakeDesktop, writer_doc
from tests.uno_stubs import install_uno_stubs

install_uno_stubs()

from uno_bridge import UNOBridge  # noqa: E402

PARAGRAPHS = ["Schemas and Types", "Body text here."]


@pytest.fixture
def bridge():
    return UNOBridge()


@pytest.fixture
def doc():
    return writer_doc(PARAGRAPHS, caret=(0, 0))


def language_at(doc, paragraph=0, offset=0):
    locale = doc.getText().locale_at((paragraph, offset))
    return f"{locale.Language}-{locale.Country}"


def test_a_replacement_can_carry_its_language(bridge, doc):
    assert language_at(doc) == "en-US"

    result = bridge.replace_range({"paragraph": 0}, "Схемы и типы",
                                  language="ru-RU", doc=doc)

    assert result["success"] is True
    assert result["language"] == "ru-RU"
    assert language_at(doc) == "ru-RU"


def test_a_replacement_without_a_language_leaves_the_locale_alone(bridge, doc):
    result = bridge.replace_range({"paragraph": 0}, "Схемы и типы", doc=doc)

    assert result["success"] is True
    assert result["language"] is None
    assert language_at(doc) == "en-US"


def test_sets_the_language_of_text_already_written(bridge, doc):
    result = bridge.set_language({"paragraph": 0}, "ru-RU", doc=doc)

    assert result["success"] is True
    assert result["language"] == "ru-RU"
    assert language_at(doc) == "ru-RU"


def test_accepts_a_bare_language_without_a_country(bridge, doc):
    result = bridge.set_language({"paragraph": 0}, "ru", doc=doc)

    assert result["success"] is True
    assert language_at(doc) == "ru-"


def test_rejects_a_language_that_is_not_a_language_tag(bridge, doc):
    for bad in ["russian please", "", "r", "ru-RUS-extra", 42]:
        result = bridge.set_language({"paragraph": 0}, bad, doc=doc)
        assert result["success"] is False, bad
        assert "language" in result["error"].lower()
    assert language_at(doc) == "en-US"


def test_rejects_an_unresolvable_address(bridge, doc):
    result = bridge.set_language({"paragraph": 99}, "ru-RU", doc=doc)

    assert result["success"] is False
    assert "no body paragraph 99" in result["error"]


def test_rejects_a_read_only_document(bridge, doc):
    doc.readonly = True

    result = bridge.set_language({"paragraph": 0}, "ru-RU", doc=doc)

    assert result["success"] is False
    assert "read-only" in result["error"].lower()
    assert language_at(doc) == "en-US"


def test_rejects_a_non_writer_document(bridge):
    result = bridge.set_language({"paragraph": 0}, "ru-RU", doc=FakeCalcDoc())

    assert result["success"] is False
    assert "writer" in result["error"].lower()


def test_setting_the_language_is_one_undo_step(bridge, doc):
    bridge.set_language({"paragraph": 0}, "ru-RU", doc=doc)

    assert doc.UndoManager.calls == [("enter", "MCP: set language"),
                                     ("leave", None)]


def test_language_tools_are_registered_and_dispatch():
    from mcp_server import LibreOfficeMCPServer

    server = LibreOfficeMCPServer()
    doc = writer_doc(PARAGRAPHS, caret=(0, 0))
    server.uno_bridge.desktop = FakeDesktop([doc])

    assert "set_language_live" in server.tools
    assert server.tools["set_language_live"]["parameters"]["required"] == [
        "address", "language"]
    assert "language" in server.tools["replace_range_live"]["parameters"]["properties"]

    result = asyncio.run(server.execute_tool(
        "set_language_live", {"address": {"paragraph": 1}, "language": "ru-RU"}))

    assert result["success"] is True
    assert language_at(doc, paragraph=1) == "ru-RU"
