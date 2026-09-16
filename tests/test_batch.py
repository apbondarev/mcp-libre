"""Tests for carrying out a plan in one call, and taking it back in one step.

Twelve paragraphs translated one call at a time are twelve round trips and
twelve entries in the Undo menu, where the reader thinks of it as one edit.
batch_live runs the plan inside a single undo context — measured on a real
Writer: contexts nest, only the outermost becomes an entry, one Ctrl+Z takes
back everything it held, and a context that wrote nothing leaves no entry to
undo at all.
"""

import pytest

from tests.fake_writer import FakeDesktop, writer_doc
from tests.uno_stubs import install_uno_stubs

install_uno_stubs()

from mcp_server import LibreOfficeMCPServer  # noqa: E402

PARAGRAPHS = ["Введение", "Operation", "{ hero }", "Response", "Конец"]


@pytest.fixture
def server():
    made = LibreOfficeMCPServer()
    doc = writer_doc(PARAGRAPHS, caret=(0, 0))
    doc.Title = "batch.odt"
    made.uno_bridge.get_active_document = lambda: doc
    made.uno_bridge.desktop = FakeDesktop([doc])
    made.document = doc
    return made


def replacing(paragraph, text):
    return {"tool": "replace_range_live",
            "parameters": {"address": {"paragraph": paragraph}, "text": text}}


def test_every_step_runs_in_order(server):
    result = server.batch_live([replacing(1, "Запрос"),
                                replacing(3, "Ответ")])

    assert result["success"] is True
    assert (result["steps"], result["done"], result["failed"]) == (2, 2, 0)
    assert server.document.getText().paragraphs[1] == "Запрос"
    assert server.document.getText().paragraphs[3] == "Ответ"


def test_the_whole_batch_is_one_undo_step(server):
    server.batch_live([replacing(1, "Запрос"), replacing(3, "Ответ")],
                      undo_title="MCP: перевод раздела")

    undo = server.document.UndoManager
    assert undo.getAllUndoActionTitles() == ("MCP: перевод раздела",)

    undo.undo()
    assert server.document.getText().paragraphs[1] == "Operation"
    assert server.document.getText().paragraphs[3] == "Response"


def test_the_undo_step_is_named_after_the_batch_by_default(server):
    server.batch_live([replacing(1, "Запрос")])

    assert server.document.UndoManager.getAllUndoActionTitles() == (
        "MCP: batch of 1 steps",)


def test_a_batch_that_writes_nothing_leaves_no_undo_entry(server):
    result = server.batch_live([{"tool": "read_paragraphs_live",
                                 "parameters": {"start": 0, "count": 2}}])

    assert result["success"] is True
    assert server.document.UndoManager.getAllUndoActionTitles() == ()


def test_the_results_of_every_step_come_back(server):
    result = server.batch_live([
        {"tool": "read_paragraphs_live", "parameters": {"start": 1, "count": 1}},
        replacing(1, "Запрос")])

    first, second = result["results"]
    assert first["tool"] == "read_paragraphs_live"
    assert first["result"]["paragraphs"][0]["text"] == "Operation"
    assert second["result"]["success"] is True


def test_a_failing_step_stops_the_batch_and_keeps_what_was_done(server):
    result = server.batch_live([replacing(1, "Запрос"),
                                replacing(99, "никуда"),
                                replacing(3, "Ответ")])

    assert result["success"] is False
    assert (result["done"], result["failed"], result["not_run"]) == (1, 1, 1)
    assert "step 1" in result["error"]
    assert server.document.getText().paragraphs[1] == "Запрос"
    assert server.document.getText().paragraphs[3] == "Response"
    assert "Ctrl+Z" in result["note"]


def test_on_error_continue_carries_on(server):
    result = server.batch_live([replacing(99, "никуда"),
                                replacing(3, "Ответ")],
                               on_error="continue")

    assert (result["done"], result["failed"], result["not_run"]) == (1, 1, 0)
    assert server.document.getText().paragraphs[3] == "Ответ"


def test_on_error_undo_puts_the_document_back(server):
    result = server.batch_live([replacing(1, "Запрос"),
                                replacing(99, "никуда")],
                               on_error="undo")

    assert result["success"] is False
    assert result["undone"] is True
    assert server.document.getText().paragraphs[1] == "Operation"
    assert server.document.UndoManager.getAllUndoActionTitles() == ()


def test_on_error_undo_does_not_undo_the_readers_own_work(server):
    # The batch writes nothing before it fails, so there is no entry of its
    # own — undoing then would take back whatever came before it.
    server.batch_live([replacing(1, "Запрос")])
    result = server.batch_live([replacing(99, "никуда")], on_error="undo")

    assert result["undone"] is False
    assert server.document.getText().paragraphs[1] == "Запрос"


def test_a_step_naming_no_tool_is_refused_before_anything_runs(server):
    result = server.batch_live([replacing(1, "Запрос"),
                                {"tool": "translate_live", "parameters": {}}])

    assert result["success"] is False
    assert "step 1" in result["error"] and "translate_live" in result["error"]
    assert server.document.getText().paragraphs[1] == "Operation"


def test_a_batch_does_not_hold_a_batch(server):
    result = server.batch_live([{"tool": "batch_live", "parameters": {}}])

    assert result["success"] is False
    assert "does not hold batches" in result["error"]


def test_parameters_must_be_an_object(server):
    result = server.batch_live([{"tool": "read_paragraphs_live",
                                 "parameters": [1, 2]}])

    assert result["success"] is False
    assert "parameters" in result["error"]


def test_too_many_steps_are_refused(server):
    result = server.batch_live([replacing(1, "x")] * 51)

    assert result["success"] is False
    assert "at most 50" in result["error"]


def test_an_empty_batch_is_refused(server):
    assert server.batch_live([])["success"] is False


def test_an_unknown_on_error_is_refused(server):
    result = server.batch_live([replacing(1, "Запрос")], on_error="rollback")

    assert result["success"] is False
    assert "on_error" in result["error"]


def test_a_step_for_another_document_is_refused(server):
    other = writer_doc(["Другой документ"], caret=(0, 0))
    other.Title = "other.odt"
    server.uno_bridge.desktop = FakeDesktop([server.document, other])

    result = server.batch_live(
        [{"tool": "replace_range_live",
          "parameters": {"address": {"paragraph": 0}, "text": "x",
                         "document": "file:///tmp/other.odt"}}],
        document="file:///tmp/batch.odt")

    assert result["success"] is False
    assert "make a batch for each document" in result["error"]


def test_a_batch_is_taken_back_even_when_the_undo_stack_is_full(server):
    undo = server.document.UndoManager
    undo.limit = 3
    for filler in ("a", "b", "c"):
        server.batch_live([replacing(0, filler)])
    assert len(undo.getAllUndoActionTitles()) == 3

    result = server.batch_live([replacing(1, "Запрос"),
                                replacing(99, "никуда")],
                               on_error="undo")

    # The stack was full, so the batch's entry pushed the oldest out and the
    # count did not move: what says the batch wrote is the top of the stack.
    assert result["undone"] is True
    assert server.document.getText().paragraphs[1] == "Operation"
