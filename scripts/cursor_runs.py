#!/usr/bin/env python3
"""Say where the caret is, then read the runs of the paragraph it stands in.

Two questions an assistant asks constantly while working on a document, and
the pair most awkward to ask by hand: the reply to a tool call arrives on the
SSE stream rather than in the answer to the POST that made it, so curl needs
two terminals. This is that dance, done once.

    python3 scripts/cursor_runs.py
    python3 scripts/cursor_runs.py --document file:///home/me/Doc.odt

Plain standard library: it talks HTTP to the running MCP server (the plugin
inside LibreOffice, or scripts/mcp_standalone.py), so any python3 will do —
no uno, no venv.
"""

import argparse
import http.client
import json
import queue
import sys
import threading
import time


class MCPClient:
    """The smallest MCP client that works: one SSE stream, POSTed requests."""

    def __init__(self, host="localhost", port=8765, timeout=300.0):
        self.host, self.port, self.timeout = host, port, timeout
        self._replies: "queue.Queue[dict]" = queue.Queue()
        self._endpoint = None
        self._failed = None
        self._ids = iter(range(1, 10 ** 9))

    def _listen(self):
        try:
            conn = http.client.HTTPConnection(self.host, self.port,
                                              timeout=self.timeout)
            conn.request("GET", "/sse", headers={"Accept": "text/event-stream"})
            stream = conn.getresponse()
        except OSError as e:
            self._failed = e
            return
        event = None
        while True:
            line = stream.readline()
            if not line:
                return
            line = line.decode("utf-8", "replace").rstrip("\r\n")
            if line.startswith("event:"):
                event = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data = line[len("data:"):].strip()
                if event == "endpoint":
                    self._endpoint = data
                else:
                    try:
                        self._replies.put(json.loads(data))
                    except ValueError:
                        pass

    def open(self):
        threading.Thread(target=self._listen, daemon=True).start()
        waited = 0.0
        while self._endpoint is None and self._failed is None and waited < 10:
            time.sleep(0.05)
            waited += 0.05
        if self._failed is not None:
            raise SystemExit(f"No MCP server on {self.host}:{self.port} "
                             f"({self._failed}). Start it with "
                             f"/usr/bin/python3 scripts/mcp_standalone.py, or "
                             f"from LibreOffice's Tools menu.")
        if self._endpoint is None:
            raise SystemExit("The server opened no session in ten seconds.")
        self.request("initialize", {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "cursor_runs", "version": "1"}})
        return self

    def request(self, method, params=None):
        wanted = next(self._ids)
        conn = http.client.HTTPConnection(self.host, self.port,
                                          timeout=self.timeout)
        conn.request("POST", self._endpoint,
                     body=json.dumps({"jsonrpc": "2.0", "id": wanted,
                                      "method": method,
                                      "params": params or {}}),
                     headers={"Content-Type": "application/json"})
        answered = conn.getresponse()
        answered.read()
        if answered.status not in (200, 202):
            raise SystemExit(f"The server refused the call: "
                             f"HTTP {answered.status}")
        while True:
            try:
                reply = self._replies.get(timeout=self.timeout)
            except queue.Empty:
                raise SystemExit(f"No answer to {method} in "
                                 f"{self.timeout:.0f}s — a call that sweeps a "
                                 f"long document can take minutes, and the "
                                 f"server answers one call at a time.")
            if reply.get("id") == wanted:
                return reply

    def call(self, tool, arguments):
        """A tool's own answer, unwrapped from the MCP envelope."""
        reply = self.request("tools/call", {"name": tool,
                                            "arguments": arguments})
        if "error" in reply:
            return {"success": False, "error": reply["error"]}
        for piece in reply.get("result", {}).get("content", []):
            if piece.get("type") == "text":
                try:
                    return json.loads(piece["text"])
                except ValueError:
                    return {"text": piece["text"]}
        return reply.get("result", {})


def show(title, answer):
    print(f"\n=== {title} ===")
    print(json.dumps(answer, ensure_ascii=False, indent=2))


def address_of(cursor):
    """Where to read the runs: the anchor the cursor came back with.

    The paragraph's *number* is not in the answer unless it was asked for —
    a paragraph has none in UNO, so counting one is a walk of the body — and
    the anchor names the same place for two UNO calls. A caret inside a table
    cell belongs to the cell's own text, which a cell address reaches.
    """
    address = cursor.get("address")
    if address:
        return address
    in_table = cursor.get("in_table") or {}
    if in_table.get("cell"):
        return {"table": in_table.get("table"), "cell": in_table["cell"]}
    index = (cursor.get("cursor") or {}).get("paragraph_index")
    return {"paragraph": index} if isinstance(index, int) else None


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=8765,
                        help="port the MCP server serves SSE on (8765)")
    parser.add_argument("--document",
                        help="URL of the document, from list_open_documents; "
                             "the active one by default")
    parser.add_argument("--timeout", type=float, default=300.0,
                        help="how long to wait for one answer, in seconds")
    args = parser.parse_args()

    client = MCPClient(args.host, args.port, args.timeout).open()
    named = {"document": args.document} if args.document else {}

    cursor = client.call("get_cursor_info_live", dict(named))
    show("get_cursor_info_live", cursor)
    if not cursor.get("success"):
        return 1

    address = address_of(cursor)
    if address is None:
        print("\nThe caret is in no body paragraph and in no table cell "
              "(a picture may be selected), so there are no runs to read.")
        return 1

    runs = client.call("read_runs_live", dict(named, address=address))
    show(f"read_runs_live {json.dumps(address, ensure_ascii=False)}", runs)
    return 0 if runs.get("success") else 1


if __name__ == "__main__":
    sys.exit(main())
