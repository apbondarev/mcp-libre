"""Tests for how the bridge decides what kind of document it is holding.

A UNO document proxy is <class 'pyuno'> and inherits none of the imported
com.sun.star.* interfaces, so isinstance() against them is always False.
Everything that dispatches on document type has to ask supportsService().
"""

import pytest

from tests.fake_writer import FakeCalcDoc, FakeUnknownDoc, writer_doc
from tests.uno_stubs import install_uno_stubs

install_uno_stubs()

from uno_bridge import UNOBridge  # noqa: E402

PARAGRAPHS = ["Alpha beta.", "Gamma delta epsilon."]


@pytest.fixture
def bridge():
    return UNOBridge()


def test_identifies_writer_document(bridge):
    doc = writer_doc(PARAGRAPHS, caret=(0, 0))

    assert bridge._get_document_type(doc) == "writer"


def test_identifies_calc_document(bridge):
    assert bridge._get_document_type(FakeCalcDoc()) == "calc"


def test_reports_unknown_for_component_without_services(bridge):
    assert bridge._get_document_type(FakeUnknownDoc()) == "unknown"


def test_document_info_counts_words_and_characters_of_writer_text(bridge):
    doc = writer_doc(PARAGRAPHS, caret=(0, 0))

    info = bridge.get_document_info(doc)

    assert info["type"] == "writer"
    assert info["word_count"] == 5
    assert info["character_count"] == len("Alpha beta.\nGamma delta epsilon.")


def test_get_text_content_returns_the_writer_text(bridge):
    doc = writer_doc(PARAGRAPHS, caret=(0, 0))

    result = bridge.get_text_content(doc)

    assert result["success"] is True
    assert result["content"] == "Alpha beta.\nGamma delta epsilon."


def test_get_text_content_rejects_a_calc_document(bridge):
    result = bridge.get_text_content(FakeCalcDoc())

    assert result["success"] is False
    assert "calc" in result["error"].lower()
