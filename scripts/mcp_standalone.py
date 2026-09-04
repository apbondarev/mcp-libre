#!/usr/bin/env python3
"""Run the plugin's MCP server outside LibreOffice, over a UNO socket.

Why this exists: the plugin normally runs *inside* LibreOffice as a registered
UNO component. When LibreOffice refuses to register a Python component — the
symptom is `unopkg add` failing with "C++ code threw St9bad_alloc" for any
Python component, including a minimal one with none of this project's code —
the extension cannot be installed at all. This script serves the same tools on
the same port without needing the extension, so an MCP client configured for
http://localhost:8765/sse keeps working unchanged.

Start LibreOffice with a UNO socket first:

    soffice --writer --norestore \\
        "--accept=socket,host=127.0.0.1,port=2002;urp;" ~/Documents/file.odt &

Then, under the system Python (the one with python3-uno; the repo venv has no
uno module):

    /usr/bin/python3 scripts/mcp_standalone.py

Known limitation, measured rather than assumed: pyuno multiplexes one socket
bridge, and concurrent calls over it interleave and return each other's state.
The server therefore handles tool calls **one at a time** — a lock serialises
them. A single MCP client asking one question at a time notices nothing; a
client that pipelines requests gets correct answers more slowly instead of
wrong answers quickly.
"""

import argparse
import os
import signal
import sys
import threading
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "plugin", "pythonpath"))

try:
    import uno
except ImportError:
    sys.exit("No uno module. Run this with /usr/bin/python3 (needs python3-uno), "
             "not the repo venv.")


def connect(uno_port, host, attempts=30):
    """Resolve a component context on a LibreOffice listening on a socket."""
    local = uno.getComponentContext()
    resolver = local.ServiceManager.createInstanceWithContext(
        "com.sun.star.bridge.UnoUrlResolver", local)
    url = (f"uno:socket,host={host},port={uno_port};urp;"
           "StarOffice.ComponentContext")
    for attempt in range(attempts):
        try:
            return resolver.resolve(url)
        except Exception as e:
            if attempt == attempts - 1:
                sys.exit(f"Could not reach LibreOffice on {host}:{uno_port}: {e}\n"
                         "Start it with --accept=socket,host=127.0.0.1,"
                         f"port={uno_port};urp;")
            time.sleep(1)


def build_server(ctx):
    """A LibreOfficeMCPServer whose bridge talks to the remote office."""
    from uno_bridge import UNOBridge
    from mcp_server import LibreOfficeMCPServer
    import mcp_server as mcp_server_module

    bridge = UNOBridge.__new__(UNOBridge)  # skip __init__: no local desktop
    bridge.ctx = ctx
    bridge.smgr = ctx.ServiceManager
    bridge.desktop = ctx.ServiceManager.createInstanceWithContext(
        "com.sun.star.frame.Desktop", ctx)

    server = LibreOfficeMCPServer.__new__(LibreOfficeMCPServer)
    server.uno_bridge = bridge
    server.tools = {}
    server._register_tools()

    # One call at a time: see the limitation in this module's docstring.
    lock = threading.Lock()
    execute = server.execute_tool

    async def serialised(tool_name, parameters):
        with lock:
            return await execute(tool_name, parameters)

    server.execute_tool = serialised

    # ai_interface asks the module for the singleton, so make ours the one.
    mcp_server_module.mcp_server = server
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--uno-port", type=int, default=2002,
                        help="port LibreOffice accepts UNO on (default 2002)")
    parser.add_argument("--uno-host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765,
                        help="port to serve MCP over SSE on (default 8765)")
    parser.add_argument("--host", default="localhost")
    args = parser.parse_args()

    ctx = connect(args.uno_port, args.uno_host)
    server = build_server(ctx)

    documents = server.uno_bridge.open_documents()
    titles = []
    for document in documents:
        try:
            titles.append(document.getURL().split("/")[-1] or "(unsaved)")
        except Exception:
            titles.append("(unnamed)")
    print(f"Connected to LibreOffice on {args.uno_host}:{args.uno_port}")
    print(f"Open documents: {titles or 'none'}")
    print(f"Registered {len(server.tools)} tools")

    from ai_interface import start_ai_interface, stop_ai_interface
    start_ai_interface(port=args.port, host=args.host)
    print(f"Serving MCP over SSE on http://{args.host}:{args.port}/sse")
    print("Ctrl+C to stop.", flush=True)

    stopping = threading.Event()

    def shut_down(signum, frame):
        stopping.set()

    signal.signal(signal.SIGINT, shut_down)
    signal.signal(signal.SIGTERM, shut_down)
    stopping.wait()

    print("\nStopping.")
    stop_ai_interface()


if __name__ == "__main__":
    main()
