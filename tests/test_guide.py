"""Tests for the manual the server hands its client.

Everything measured about this document model lives in CLAUDE.md, where an
assistant driving the server over MCP never sees it — so the short form goes
out with the initialize result, and the same text becomes the skill shipped
beside the server. These check that it is sent, that it stays true to the
tools it names, and that the two copies cannot drift.
"""

import json
import re
import subprocess
import sys

import pytest

from tests.uno_stubs import install_uno_stubs

install_uno_stubs()

from mcp_guide import INSTRUCTIONS  # noqa: E402
from mcp_server import LibreOfficeMCPServer  # noqa: E402

# Words in backticks that name a parameter, a value or a key rather than a
# tool. Anything else backticked has to be a tool this server registers.
NOT_TOOLS = {
    "language", "flatten", "anchors", "runs", "on_error", "track_changes",
    "paragraphs_before", "paragraphs_after", "replace", "true", "false",
    "undo", "anchor_text", "instructions", "code", "elapsed_ms",
    "reply_to", "replies", "threads", "with_replies", "author", "change_id",
    "all", "address", "tracked", "document", "comment_id", "resolved",
    "unresolved", "authors", "reference", "link", "allow_protected",
    "from_style", "include_others", "text_before", "text_after",
    "text_with_formulas", "text", "formulas", "start", "through", "more", "held", "count", "paragraph",
    "paragraphs_selected", "contains_table", "length", "offset", "type",
    "anchorId", "level_from", "number",
}


@pytest.fixture
def tools():
    server = LibreOfficeMCPServer.__new__(LibreOfficeMCPServer)
    server.tools = {}
    server._register_tools()
    return set(server.tools)


def backticked():
    return set(re.findall(r"`([a-z][a-z_]*)`", INSTRUCTIONS))


def test_every_tool_the_guide_names_is_a_tool_this_server_has(tools):
    named = {word for word in backticked() if word not in NOT_TOOLS}

    unknown = {word for word in named
               if word not in tools and f"{word}_live" not in tools}

    assert unknown == set(), (
        f"the guide sends a caller to tools that do not exist: {unknown}")


def test_the_guide_covers_every_form_an_address_takes():
    for form in ('"paragraph"', '"through"', '"table"', '"cell"',
                 '"selection"', '"anchor"'):
        assert form in INSTRUCTIONS, f"{form} is not explained"


def test_the_guide_says_what_a_refusal_means():
    assert "refuse" in INSTRUCTIONS
    assert "flatten" in INSTRUCTIONS


def test_the_initialize_result_carries_the_guide():
    import queue

    import ai_interface

    # The reply goes to the session's own queue, the way it reaches an open
    # SSE stream.
    answers = queue.Queue()
    with ai_interface._sessions_lock:
        ai_interface._sessions["a-session"] = answers
    try:
        ai_interface._handle_mcp_request("a-session", {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"}})
    finally:
        with ai_interface._sessions_lock:
            ai_interface._sessions.pop("a-session", None)

    assert not answers.empty(), "initialize answered nothing"
    # What goes on the queue is the SSE frame, bytes and all.
    frame = answers.get_nowait()
    if isinstance(frame, bytes):
        frame = frame.decode("utf-8")
    payload = json.loads(frame.split("data: ", 1)[1].strip())
    result = payload["result"]
    assert result["instructions"] == INSTRUCTIONS
    assert result["protocolVersion"] == "2024-11-05"


def test_the_shipped_skill_is_what_the_guide_would_write():
    checked = subprocess.run(
        [sys.executable, "scripts/write_skill.py", "--check"],
        capture_output=True, text=True)

    assert checked.returncode == 0, checked.stdout + checked.stderr
