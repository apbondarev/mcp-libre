"""Tests for how a document's own text crosses the wire.

The server answers with the words it read out of Writer, and `json.dumps`
escapes every non-ASCII character unless told otherwise: "Введение" used to
leave the transport as six ASCII characters per letter — 4.8 times the bytes,
unreadable to a human watching a tool call, and something a model has to
decode before it can copy the text back into `replace_runs_live`. SSE is UTF-8
by specification, so the text now travels as itself.

What has to survive with it is the framing: a newline inside a paragraph must
stay escaped, or it would end the `data:` line and cut the frame in two.
"""

import json
import queue
import re

import pytest

from tests.fake_writer import FakeDesktop, writer_doc
from tests.uno_stubs import install_uno_stubs

install_uno_stubs()

import ai_interface  # noqa: E402
from ai_interface import _make_sse  # noqa: E402
from mcp_server import LibreOfficeMCPServer  # noqa: E402

PARAGRAPHS = ["Введение", "Второй абзац: здесь есть слово ПОМЕЧЕНО и число 42."]


@pytest.fixture
def server():
    made = LibreOfficeMCPServer()
    doc = writer_doc(PARAGRAPHS, caret=(0, 0))
    made.uno_bridge.get_active_document = lambda: doc
    made.uno_bridge.desktop = FakeDesktop([doc])
    made.document = doc
    return made


def frame_for(monkeypatch, server, message):
    """Run one JSON-RPC message and return the SSE frame it queued, as text."""
    monkeypatch.setattr(ai_interface, "get_mcp_server", lambda: server)
    answers = queue.Queue()
    with ai_interface._sessions_lock:
        ai_interface._sessions["a-session"] = answers
    try:
        ai_interface._handle_mcp_request("a-session", message)
    finally:
        with ai_interface._sessions_lock:
            ai_interface._sessions.pop("a-session", None)

    assert not answers.empty(), "the request answered nothing"
    return answers.get_nowait().decode("utf-8")


def test_the_frame_carries_the_text_as_itself():
    frame = _make_sse("message", {"text": "Заголовок теста"}).decode("utf-8")

    assert "Заголовок теста" in frame
    assert "\\u04" not in frame


def test_a_tool_result_arrives_in_the_documents_own_words(server, monkeypatch):
    frame = frame_for(monkeypatch, server, {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "read_paragraphs_live",
                   "arguments": {"start": 0, "count": 1}}})

    payload = json.loads(frame.split("data: ", 1)[1].strip())
    text = payload["result"]["content"][0]["text"]

    assert "Введение" in text
    assert "\\u04" not in text
    # Still JSON a client parses, holding the paragraph it asked for.
    assert json.loads(text)["paragraphs"][0]["text"] == "Введение"


def test_a_paragraph_break_stays_inside_one_data_line():
    frame = _make_sse("message", {"text": "первая\nвторая"}).decode("utf-8")

    data_lines = [line for line in frame.split("\n")
                  if line.startswith("data: ")]

    assert len(data_lines) == 1
    assert json.loads(data_lines[0][len("data: "):])["text"] == "первая\nвторая"


def test_nothing_on_the_wire_escapes_non_ascii_any_more():
    with open("plugin/pythonpath/ai_interface.py", encoding="utf-8") as handle:
        body = handle.read()

    calls = re.findall(r"json\.dumps\([^)]*", body)

    assert calls, "the transport stopped serialising — has it moved?"
    bare = [call for call in calls if "ensure_ascii=False" not in call]
    assert bare == [], f"json.dumps that would escape the text: {bare}"
