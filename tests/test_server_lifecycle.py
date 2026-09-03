"""Tests for starting and stopping the embedded server.

These cover the two defects behind a LibreOffice crash on "Start MCP Server":
the action handed UNO work to a background thread and then blocked the main
thread in a modal dialog, and stopping could never complete while an SSE
stream was open.
"""

import os
import threading
import time
import urllib.request

import pytest

from tests.uno_stubs import install_uno_stubs

install_uno_stubs()

# Keep the plugin's own log out of the developer's /tmp file
os.environ.setdefault("MCP_EXTENSION_LOG", "/tmp/mcp_extension_test.log")

import ai_interface  # noqa: E402
import mcp_server  # noqa: E402
import registration  # noqa: E402
from ai_interface import AIInterface  # noqa: E402


@pytest.fixture
def extension():
    ext = registration.MCPExtension(ctx=None)
    registration._server_started = False
    registration._mcp_server = None
    registration._ai_interface = None
    return ext


@pytest.fixture
def dialogs(extension, monkeypatch):
    """Capture what the extension would show the user."""
    shown = []
    monkeypatch.setattr(extension, "_show_dialog",
                        lambda title, message: shown.append((title, message)))
    return shown


def test_start_runs_on_the_calling_thread(extension, dialogs, monkeypatch):
    """UNO work must stay on LibreOffice's main thread.

    Moving it to another thread and then opening a modal dialog kills the
    process: the dialog's nested event loop holds the SolarMutex, and Python
    has no way to acquire it.
    """
    threads = []

    def record():
        threads.append(threading.current_thread())
        return "started"

    monkeypatch.setattr(extension, "_start_mcp_server", record)

    extension._execute_action("start_mcp_server")

    assert threads == [threading.current_thread()]


def test_start_reports_the_real_failure_instead_of_claiming_success(
        extension, dialogs, monkeypatch):
    def explode():
        raise RuntimeError("port 8765 already in use")

    monkeypatch.setattr(mcp_server, "get_mcp_server", explode)

    extension._execute_action("start_mcp_server")

    assert len(dialogs) == 1
    assert "failed" in dialogs[0][1].lower()
    assert "port 8765 already in use" in dialogs[0][1]
    assert registration._server_started is False


def test_start_reports_success_only_after_the_server_is_up(
        extension, dialogs, monkeypatch):
    monkeypatch.setattr(extension, "_start_mcp_server",
                        lambda: "MCP Server is started\nhttp://localhost:8765")

    extension._execute_action("start_mcp_server")

    assert dialogs == [("MCP Server", "MCP Server is started\nhttp://localhost:8765")]


def test_stopping_a_server_that_never_started_says_so(extension, dialogs):
    extension._execute_action("stop_mcp_server")

    assert len(dialogs) == 1
    assert "not running" in dialogs[0][1].lower()


def test_stop_closes_an_open_sse_stream_promptly():
    """An open SSE handler must not keep the server from shutting down."""
    interface = AIInterface(port=0, host="127.0.0.1")
    interface.start()
    port = interface.server.server_address[1]
    opened = threading.Event()

    def hold_stream():
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/sse", timeout=30) as r:
                opened.set()
                r.readline()  # the endpoint event
                r.read()      # blocks until the server closes the stream
        except Exception:
            opened.set()

    threading.Thread(target=hold_stream, daemon=True).start()
    assert opened.wait(timeout=10), "SSE stream never opened"
    time.sleep(0.5)

    done = threading.Event()
    threading.Thread(target=lambda: (interface.stop(), done.set()), daemon=True).start()

    try:
        assert done.wait(timeout=10), \
            "stop() did not finish while an SSE stream was open"
    finally:
        # Release any handler still waiting, so a failure here cannot wedge the
        # whole pytest process on a non-daemon handler thread.
        with ai_interface._sessions_lock:
            for queue in ai_interface._sessions.values():
                queue.put(None)
        done.wait(timeout=10)
