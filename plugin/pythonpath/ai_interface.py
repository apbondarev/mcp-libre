"""
LibreOffice MCP Extension - AI Interface Module

Implements MCP over SSE transport (stdlib only, no external deps).

SSE transport protocol:
  GET  /sse               - client connects, receives SSE stream
  POST /messages?session= - client sends JSON-RPC, responses flow over SSE
"""

import asyncio
import json
import logging
import queue
import threading
import uuid
from http.server import BaseHTTPRequestHandler
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse
import socketserver

from mcp_server import get_mcp_server

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# session_id -> Queue of SSE message strings
_sessions: Dict[str, queue.Queue] = {}
_sessions_lock = threading.Lock()


def _make_sse(event: str, data: Any) -> bytes:
    payload = json.dumps(data) if not isinstance(data, str) else data
    return f"event: {event}\ndata: {payload}\n\n".encode()


def _jsonrpc_response(req_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _jsonrpc_error(req_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _handle_mcp_request(session_id: str, msg: dict):
    """Process one JSON-RPC MCP message and push response to the session queue."""
    mcp = get_mcp_server()
    method = msg.get("method", "")
    req_id = msg.get("id")

    if method == "initialize":
        resp = _jsonrpc_response(req_id, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "libreoffice-mcp", "version": "1.0.0"},
        })
    elif method == "notifications/initialized":
        return  # notification, no response
    elif method == "tools/list":
        tools = [
            {
                "name": t["name"],
                "description": t["description"],
                "inputSchema": t["parameters"],
            }
            for t in mcp.get_tool_list()
        ]
        resp = _jsonrpc_response(req_id, {"tools": tools})
    elif method == "tools/call":
        params = msg.get("params", {})
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})
        try:
            loop = asyncio.new_event_loop()
            result = loop.run_until_complete(mcp.execute_tool(tool_name, arguments))
            loop.close()
            resp = _jsonrpc_response(req_id, {
                "content": [{"type": "text", "text": json.dumps(result)}]
            })
        except Exception as e:
            resp = _jsonrpc_error(req_id, -32000, str(e))
    elif method == "ping":
        resp = _jsonrpc_response(req_id, {})
    else:
        if req_id is None:
            return  # unknown notification, ignore
        resp = _jsonrpc_error(req_id, -32601, f"Method not found: {method}")

    with _sessions_lock:
        q = _sessions.get(session_id)
    if q:
        q.put(_make_sse("message", resp))


class MCPRequestHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/sse":
            self._handle_sse()
        elif parsed.path == "/health":
            self._send_json(200, {"status": "healthy"})
        elif parsed.path == "/":
            mcp = get_mcp_server()
            self._send_json(200, {
                "name": "LibreOffice MCP Extension",
                "version": "1.0.0",
                "description": "MCP server integrated into LibreOffice",
                "mcp_endpoint": "GET /sse",
                "tools_count": len(mcp.tools),
            })
        elif parsed.path == "/tools":
            mcp = get_mcp_server()
            self._send_json(200, {
                "tools": mcp.get_tool_list(),
                "count": len(mcp.tools),
            })
        else:
            self._send_json(404, {"error": "Not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/messages":
            qs = parse_qs(parsed.query)
            session_ids = qs.get("sessionId", [])
            if not session_ids:
                self._send_json(400, {"error": "Missing sessionId"})
                return
            self._handle_message(session_ids[0])
        else:
            self._send_json(404, {"error": "Not found"})

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def _handle_sse(self):
        session_id = str(uuid.uuid4())
        q: queue.Queue = queue.Queue()
        with _sessions_lock:
            _sessions[session_id] = q

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self._cors()
        self.end_headers()

        try:
            # Send the endpoint event so the client knows where to POST
            endpoint_event = _make_sse("endpoint", f"/messages?sessionId={session_id}")
            self.wfile.write(endpoint_event)
            self.wfile.flush()

            while True:
                try:
                    chunk = q.get(timeout=15)
                    if chunk is None:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
                except queue.Empty:
                    # keepalive ping
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            with _sessions_lock:
                _sessions.pop(session_id, None)

    def _handle_message(self, session_id: str):
        with _sessions_lock:
            if session_id not in _sessions:
                self._send_json(404, {"error": "Unknown session"})
                return

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode() if length else "{}"
        try:
            msg = json.loads(body)
        except json.JSONDecodeError:
            self._send_json(400, {"error": "Invalid JSON"})
            return

        threading.Thread(
            target=_handle_mcp_request, args=(session_id, msg), daemon=True
        ).start()

        self.send_response(202)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send_json(self, code: int, data: dict):
        body = json.dumps(data).encode()
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def log_message(self, fmt, *args):
        logger.info(f"{self.client_address[0]} - {fmt % args}")


class AIInterface:
    def __init__(self, port: int = 8765, host: str = "localhost"):
        self.port = port
        self.host = host
        self.server = None
        self.server_thread = None
        self.running = False

    def start(self):
        if self.running:
            return
        socketserver.ThreadingTCPServer.allow_reuse_address = True
        self.server = socketserver.ThreadingTCPServer((self.host, self.port), MCPRequestHandler)
        self.running = True
        self.server_thread = threading.Thread(target=self._run, daemon=True)
        self.server_thread.start()
        logger.info(f"MCP SSE server started on http://{self.host}:{self.port}")

    def stop(self):
        if not self.running:
            return
        self.running = False
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        # Signal all open SSE connections to close
        with _sessions_lock:
            for q in _sessions.values():
                q.put(None)

    def _run(self):
        try:
            self.server.serve_forever()
        except Exception as e:
            if self.running:
                logger.error(f"Server error: {e}")
        finally:
            self.running = False

    def is_running(self) -> bool:
        return self.running

    def get_status(self) -> dict:
        return {
            "running": self.running,
            "url": f"http://{self.host}:{self.port}",
        }


_ai_interface: Optional[AIInterface] = None


def get_ai_interface(port: int = 8765, host: str = "localhost") -> AIInterface:
    global _ai_interface
    if _ai_interface is None:
        _ai_interface = AIInterface(port, host)
    return _ai_interface


def start_ai_interface(port: int = 8765, host: str = "localhost") -> AIInterface:
    iface = get_ai_interface(port, host)
    if not iface.is_running():
        iface.start()
    return iface


def stop_ai_interface():
    global _ai_interface
    if _ai_interface:
        _ai_interface.stop()
