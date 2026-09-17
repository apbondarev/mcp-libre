"""Tests for what every result carries: a code to branch on, and what it cost.

A refusal used to be a sentence of English, so a caller could only match on
it or give up; and a slow tool was something a human with a stopwatch found,
since nothing in the answer said how long it took. Every result now comes
back through one place that stamps `elapsed_ms`, and every refusal names one
of a small closed set of codes.
"""

import asyncio
import glob
import re

import pytest

from tests.fake_writer import FakeDesktop, writer_doc
from tests.uno_stubs import install_uno_stubs

install_uno_stubs()

from mcp_server import LibreOfficeMCPServer  # noqa: E402
from uno_values import ERROR_CODES, refusal  # noqa: E402

PARAGRAPHS = ["Введение", "Operation", "{ hero }", "Response", "Конец"]


@pytest.fixture
def server():
    made = LibreOfficeMCPServer()
    doc = writer_doc(PARAGRAPHS, caret=(0, 0))
    doc.Title = "contract.odt"
    made.uno_bridge.get_active_document = lambda: doc
    made.uno_bridge.desktop = FakeDesktop([doc])
    made.document = doc
    return made


def test_every_code_written_in_the_source_is_one_of_the_known_ones():
    used = set()
    for path in glob.glob("plugin/pythonpath/*.py"):
        with open(path, encoding="utf-8") as handle:
            body = handle.read()
        used.update(re.findall(r'"code": "([A-Z_]+)"', body))
        used.update(re.findall(r'refusal\(\s*"([A-Z_]+)"', body))

    assert used, "no codes found at all — has the contract gone?"
    assert used <= ERROR_CODES, f"codes nobody declared: {used - ERROR_CODES}"


def test_the_helper_refuses_a_code_nobody_declared():
    with pytest.raises(ValueError):
        refusal("KAPUT", "the sky fell in")


def test_a_refusal_names_a_code(server):
    result = server._run_tool("replace_range_live",
                              {"address": {"paragraph": 99}, "text": "x"})

    assert result["success"] is False
    assert result["code"] == "INVALID_ADDRESS"


def test_the_codes_tell_the_kinds_of_refusal_apart(server):
    cases = {
        "INVALID_ADDRESS": ("replace_range_live",
                            {"address": {"paragraph": 99}, "text": "x"}),
        "INVALID_PARAMETER": ("read_paragraphs_live", {"start": -3}),
        "NOT_FOUND": ("read_table_live", {"name": "Нетакой"}),
        "WOULD_LOSE_FORMATTING": ("replace_range_live",
                                  {"address": {"paragraph": 0}, "text": "x"}),
    }
    doc = server.document
    # A paragraph of several runs, so replacing it flatly is refused.
    doc.getText().portions[0] = [
        {"text": "Введение", "properties": {"CharWeight": 150.0}},
        {"text": " и дальше"}]
    doc.getText().paragraphs[0] = "Введение и дальше"

    for code, (tool, parameters) in cases.items():
        assert server._run_tool(tool, parameters).get("code") == code, tool


def test_an_unknown_tool_says_so_with_a_code(server):
    assert server._run_tool("translate_live", {})["code"] == "NOT_FOUND"


def test_a_tool_called_wrongly_says_which_kind_of_wrong(server):
    result = server._run_tool("read_paragraphs_live", {"nonsense": 1})

    assert result["code"] == "INVALID_PARAMETER"


def test_every_result_says_what_it_cost(server):
    worked = server._run_tool("read_paragraphs_live", {"start": 0, "count": 2})
    refused = server._run_tool("read_table_live", {"name": "Нетакой"})

    for result in (worked, refused):
        assert isinstance(result["elapsed_ms"], int)
        assert result["elapsed_ms"] >= 0


def test_the_transport_carries_the_same_stamps(server):
    result = asyncio.run(server.execute_tool("read_paragraphs_live",
                                             {"start": 0, "count": 1}))

    assert "elapsed_ms" in result


def test_a_batch_takes_the_code_of_the_step_that_failed(server):
    result = server.batch_live([
        {"tool": "replace_range_live",
         "parameters": {"address": {"paragraph": 99}, "text": "x"}}])

    assert result["success"] is False
    assert result["code"] == "INVALID_ADDRESS"
    assert result["results"][0]["result"]["elapsed_ms"] >= 0


def test_no_tool_answers_a_refusal_without_a_code(server):
    # Every tool called with nothing at all: whatever it says, it says it in
    # a word as well as in a sentence.
    uncoded = []
    for name in sorted(server.tools):
        outcome = server._run_tool(name, {})
        if isinstance(outcome, dict) and outcome.get("success") is False \
                and outcome.get("code") not in ERROR_CODES:
            uncoded.append((name, outcome.get("error")))

    assert uncoded == []
