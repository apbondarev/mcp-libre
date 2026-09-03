"""Tests for reporting misspellings with addresses that can be acted on.

Each hit carries the address of the word, so the caller can hand it straight
to replace_range_live. Words are checked against the language of the run they
sit in, not the paragraph's or the document's, so an English term inside a
Russian sentence is judged as English.
"""

import asyncio

import pytest

from tests.fake_writer import (
    FakeCalcDoc,
    FakeDesktop,
    FakeLocale,
    FakeSpellChecker,
    writer_doc,
)
from tests.uno_stubs import install_uno_stubs

install_uno_stubs()

from uno_bridge import UNOBridge  # noqa: E402

RU = FakeLocale("ru", "RU")
EN = FakeLocale("en", "US")
DE = FakeLocale("de", "DE")

VOCABULARY = {
    "ru-RU": {"Схемы", "и", "типы", "клиентов", "описывают"},
    "en-US": {"type", "system", "the", "and", "Types", "Schemas"},
}
SUGGESTIONS = {"Схеммы": ["Схемы", "Схем"], "GraphQL": ["Graph Ql"]}


@pytest.fixture
def bridge():
    speller = FakeSpellChecker(vocabulary=VOCABULARY, suggestions=SUGGESTIONS)
    bridge = UNOBridge()
    bridge._speller = speller
    return bridge


def test_reports_a_misspelled_word_with_its_address(bridge):
    doc = writer_doc(["Схеммы и типы"], caret=(0, 0),
                     default_locale=("ru", "RU"))

    result = bridge.check_spelling(doc=doc)

    assert result["success"] is True
    assert len(result["misspelled"]) == 1
    hit = result["misspelled"][0]
    assert hit["word"] == "Схеммы"
    assert hit["address"] == {"paragraph": 0, "offset": 0, "length": 6}
    assert hit["suggestions"] == ["Схемы", "Схем"]
    assert hit["language"] == "ru-RU"


def test_the_reported_address_resolves_to_the_misspelled_word(bridge):
    doc = writer_doc(["Все Схеммы тут"], caret=(0, 0),
                     default_locale=("ru", "RU"))

    hit = bridge.check_spelling(doc=doc)["misspelled"][0]

    assert bridge._resolve_address(doc, hit["address"]).getString() == hit["word"]


def test_judges_each_run_in_its_own_language(bridge):
    doc = writer_doc(["Схемы описывают type system и клиентов"], caret=(0, 0),
                     portions={0: [("Схемы описывают ", RU),
                                   ("type system", EN),
                                   (" и клиентов", RU)]})

    result = bridge.check_spelling(doc=doc)

    assert result["misspelled"] == []
    assert ("type", "en-US") in bridge._speller.checked
    assert ("Схемы", "ru-RU") in bridge._speller.checked


def test_does_not_check_a_language_with_no_dictionary(bridge):
    doc = writer_doc(["Rechtschreibung ist schwer"], caret=(0, 0),
                     portions={0: [("Rechtschreibung ist schwer", DE)]})

    result = bridge.check_spelling(doc=doc)

    assert result["misspelled"] == []
    assert result["skipped_languages"] == ["de-DE"]


def test_checks_only_the_addressed_paragraph_when_asked(bridge):
    doc = writer_doc(["Схеммы", "Схеммы"], caret=(0, 0),
                     default_locale=("ru", "RU"))

    result = bridge.check_spelling(address={"paragraph": 1}, doc=doc)

    assert [hit["address"]["paragraph"] for hit in result["misspelled"]] == [1]


def test_caps_the_report_but_states_the_true_count(bridge):
    doc = writer_doc(["Схеммы " * 10], caret=(0, 0), default_locale=("ru", "RU"))

    result = bridge.check_spelling(max_results=3, doc=doc)

    assert len(result["misspelled"]) == 3
    assert result["total_misspelled"] == 10
    assert result["truncated"] is True


def test_ignores_numbers_and_punctuation(bridge):
    doc = writer_doc(["Схемы, 42 и типы — 3.14!"], caret=(0, 0),
                     default_locale=("ru", "RU"))

    result = bridge.check_spelling(doc=doc)

    assert result["misspelled"] == []
    assert all(not word.isdigit() for word, _ in bridge._speller.checked)


def test_reports_nothing_for_a_clean_document(bridge):
    doc = writer_doc(["Схемы и типы"], caret=(0, 0), default_locale=("ru", "RU"))

    result = bridge.check_spelling(doc=doc)

    assert result["success"] is True
    assert result["misspelled"] == []
    assert result["total_misspelled"] == 0


def test_rejects_a_non_writer_document(bridge):
    result = bridge.check_spelling(doc=FakeCalcDoc())

    assert result["success"] is False
    assert "writer" in result["error"].lower()


def test_tool_is_registered_and_dispatches():
    from mcp_server import LibreOfficeMCPServer

    server = LibreOfficeMCPServer()
    doc = writer_doc(["Схеммы и типы"], caret=(0, 0), default_locale=("ru", "RU"))
    server.uno_bridge.desktop = FakeDesktop([doc])
    server.uno_bridge._speller = FakeSpellChecker(vocabulary=VOCABULARY,
                                                  suggestions=SUGGESTIONS)

    assert "check_spelling_live" in server.tools

    result = asyncio.run(server.execute_tool("check_spelling_live", {}))

    assert result["misspelled"][0]["word"] == "Схеммы"
