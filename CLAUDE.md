# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
uv sync                              # install dependencies

./quick-test.sh test                 # built-in functionality tests (= python src/main.py --test)
./quick-test.sh server               # start the stdio MCP server
./quick-test.sh client               # interactive in-process MCP client (tests/test_client.py)
./mcp-helper.sh check                # verify LibreOffice/Python/uv prerequisites
./mcp-helper.sh demo                 # interactive demo (examples/)
./generate-config.sh claude          # write ~/.config/claude/claude_desktop_config.json with real paths

uv run pytest tests/ -v              # 28 tests
uv run pytest tests/test_cursor_info.py                   # no LibreOffice needed
uv run pytest tests/test_insert_fix.py::test_edge_cases   # single test
```

Two kinds of test live here. `test_cursor_info.py` and `test_document_type_dispatch.py` are real asserting tests that run in the venv, because `tests/uno_stubs.py` fakes the `uno` and `com.sun.star.*` modules so `plugin/pythonpath/` becomes importable — see the caveat in `tests/fake_writer.py` about what fakes cannot prove. `test_client.py` and `test_insert_fix.py` are older print-driven scripts that shell out to real LibreOffice and return booleans instead of asserting, so their green runs prove little — read their output. `test_client.py::test_mcp_client` fails on collection because `pytest-asyncio` is not enabled (no `asyncio_mode` in `pyproject.toml`); that predates any current work.

No linter or formatter is configured; `ruff` is not a dependency despite what `QWEN.md` says.

### Plugin (LibreOffice extension)

```bash
cd plugin
./install.sh install      # build .oxt + unopkg remove/add (restart LibreOffice afterwards)
./install.sh status       # unopkg list + curl localhost:8765/health
./install.sh test         # plugin/test_plugin.py against the HTTP API (needs `requests`, running soffice)
./build.sh                # package into ../build/libreoffice-mcp-extension-1.0.0.oxt only
tail -f /tmp/mcp_extension.log   # the only usable log — UNO swallows stderr
```

## Architecture

Two **independent** MCP server implementations live here. They share no code; a change in one is not a change in the other.

### 1. External server — `src/libremcp.py` (14 tools, stdio)

A single-file FastMCP server that manipulates documents **as files on disk**, driving LibreOffice through `subprocess` (`libreoffice --headless --convert-to ...`). It never talks to a running LibreOffice instance, so it has no concept of the active document, the cursor, or the selection.

Consequences worth knowing before adding a tool:

- `_run_libreoffice_command` tries `libreoffice`, `loffice`, `soffice` in order.
- Text extraction (`read_document_text`) converts to `.txt` in a temp dir, then falls back to unzipping `content.xml` (`_extract_text_from_odt`), then to reading raw bytes.
- **Editing is destructive by design.** `insert_text_at_position` reads all text, concatenates, and *recreates* the file (`_recreate_writer_document` → LibreOffice conversion → `_create_minimal_odt` hand-written ODT zip). All formatting, styles, and images are lost. A `.backup` copy is restored on failure. `_insert_text_writer_document` is dead code — it builds a UNO macro string and then `return False` unconditionally.
- Resources: `documents://` (rglob over `~/Documents`, `~/Desktop`, cwd) and `document://{path}`.
- `main()` handles `--test`, `--help`, `--version` before `mcp.run()`; startup chatter goes to stderr so stdio JSON-RPC stays clean.

Entry points: `src/main.py` does a flat `import libremcp`, which works only because Python puts the script's own directory on `sys.path` — hence `PYTHONPATH=<repo>/src` in the config templates. The root `libremcp.py` is a 6-line vestigial stub, not the server; `pyproject.toml`'s `[project.scripts]`/hatch `packages` still point at stale paths.

### 2. Embedded plugin — `plugin/pythonpath/` (16 tools, SSE over HTTP :8765)

Runs *inside* LibreOffice via UNO, so it acts on live open documents with no file I/O. Four layers:

- `registration.py` — the UNO component. Registered as a `com.sun.star.frame.ProtocolHandler` (see `ProtocolHandler.xcu`) for the `org.mcp.libreoffice.extension:` protocol; `Addons.xcu` menu items dispatch URLs like `...:start_mcp_server` into `MCPExtension._execute_action`. Server state lives in module-level globals shared across instances; start/stop run on daemon threads.
- `ai_interface.py` — MCP SSE transport on a `BaseHTTPRequestHandler`: `GET /sse` opens a stream and mints a session, `POST /messages?sessionId=` accepts JSON-RPC whose replies are pushed back over that session's queue. Also `GET /health`, `/`, `/tools` for debugging. Implements `initialize`, `tools/list`, `tools/call`, `ping` by hand (protocol version `2024-11-05`).
- `mcp_server.py` — `LibreOfficeMCPServer` (lazy singleton via `get_mcp_server()`), a `self.tools` dict of `{description, parameters (JSON Schema), handler}` plus thin `*_live` handlers.
- `uno_bridge.py` — the only place that touches UNO objects (`Desktop`, `XTextDocument`, `XSpreadsheetDocument`, `PropertyValue`).

**Adding a plugin tool means three edits**: a `UNOBridge` method, a `*_live` handler, and a `self.tools[...]` entry in `_register_tools` (the JSON Schema there is what clients see — `execute_tool` splats it as `**parameters`).

Hard constraints on plugin code:

- It runs under LibreOffice's bundled Python: **stdlib only**. No `pip` deps, no `mcp`, no `httpx`, no `pydantic`. That's why the SSE transport and JSON-RPC are hand-rolled.
- Errors are returned as `{"success": False, "error": ...}` dicts rather than raised, since exceptions inside UNO callbacks vanish.
- **Never do UNO work on a thread of your own while the main thread shows a modal dialog.** A dialog runs a nested event loop holding the SolarMutex; a Python thread cannot acquire it, and the process dies with no traceback — `/tmp/mcp_extension.log` simply stops. This crashed LibreOffice on "Start MCP Server" until `_execute_action` was made synchronous. UNO from an HTTP handler thread is fine while the main thread sits in its normal event loop, which is why tool calls work.
- `AIInterface.stop()` signals open SSE sessions *before* `shutdown()`/`server_close()`, and `MCPHTTPServer` sets `daemon_threads = True`. Without both, one open stream blocks shutdown forever, because `server_close()` joins non-daemon handler threads.
- `MCP_EXTENSION_LOG` overrides the log path, which is how tests avoid appending to the file you are tailing.
- `plugin/description.xml` `identifier`/`version` must stay in sync with the hardcoded `org.mcp.libreoffice.extension` in `install.sh` and `build.sh`'s `VERSION`; `META-INF/manifest.xml` lists exactly which files LibreOffice loads.
- **`isinstance` never works on UNO objects.** Every proxy is `<class 'pyuno'>` and inherits none of the imported `com.sun.star.*` interfaces, so `isinstance(doc, XTextDocument)` is always False — silently, since the fallback branch just reports "unknown". Use the `_supports(obj, service)` helper in `uno_bridge.py` with a service name instead. This defect had disabled `get_text_content`, `insert_text`, `format_text` and every document-type report until it was fixed.
- A text range belongs to the text that owns it. Inside a table cell or a frame that is *not* `doc.getText()`, and passing a foreign range to the body text throws "End of content node doesn't have the proper start node" — so build cursors from `range.getText()`, as `get_cursor_info` does.
- `insertString(cursor, text, bAbsorb)` — the last flag decides insert vs replace. `insert_text` passes `False`, so with a selection it inserts at the selection start and leaves the original: that is why "translate and replace" produced both texts. `replace_selection` rewrites the resolved range with `setString` instead.
- Spell checking works, grammar does not: create `com.sun.star.linguistic2.SpellChecker` **directly** (the one from `LinguServiceManager` resolves in pyuno to the `XSpellChecker1` overload wanting a numeric language id, and rejects any `Locale` with "Type 17 is not supported"). `getProofreader` is absent and the `Proofreader` service reports zero locales, so punctuation and grammar need LanguageTool installed as an extension.
- Writer picks the spelling dictionary from a run's `CharLocale`, so text written over text in another language is underlined word by word even when correct — `'Схемы'` checked as en-US is a "misspelling" with no suggestions. The replacement tools take a `language` tag and set the locale; `set_language` fixes what is already written. A paragraph's `createEnumeration()` yields text portions, each with its own `CharLocale`, which is how a run's real language is read.
- Mutations wrap the edit in `doc.UndoManager.enterUndoContext(...)` / `leaveUndoContext()` (the leave in a `finally`), so one assistant action is one Ctrl+Z, and check `doc.isReadonly()` first. `track_changes` defaults to **false**: with `RecordChanges = True` a replacement keeps the original struck through until accepted, which users read as the replacement having failed.
- `desktop.getCurrentComponent()` follows the focused frame and is **not always a document**: a modal dialog (including the extension's own status box) or the Start Center answers instead, supporting no document service and carrying no `Title`. `get_active_document()` therefore checks with `_is_document()` and falls back to `open_documents()[0]`; without that, every tool reported "not a Writer document" while one was open. Frames are not documents either — enumerate `getComponents()` and filter.
- Driving the plugin's SSE server from **one** out-of-process client over a single pyuno socket bridge is only reliable one call at a time; concurrent calls interleave and return each other's state. That is a property of the test rig, not of the plugin — in-process concurrent tool calls behave correctly. Keep `tests/live/*` and any SSE probe sequential.
- UNO objects can never be compared with `is`: pyuno mints a fresh proxy per call, so `hit.getText() is doc.getText()` is False even for a body-text hit. Compare ranges through `compareRegionStarts` instead, which works across proxies.
- Text is addressed one way: `{"paragraph": i, "offset": k, "length": n}` (body paragraphs, tables skipped) or `{"selection": true}`. `_locate_range` produces an address from a range, `_resolve_address` turns one back into a range, and every tool goes through them — see `docs/superpowers/specs/2026-08-18-writer-text-tools-design.md`.
- `tests/live/writer_tools_check.py` checks the bridge against a real headless LibreOffice. Run it with `/usr/bin/python3` (the venv has no `uno`) whenever bridge code changes; a green pytest run alone does not mean the UNO calls work.

## Conventions

- Structured returns: the external server uses Pydantic models (`DocumentInfo`, `TextContent`, `ConversionResult`, `SpreadsheetData`) so MCP clients get schemas; the plugin returns plain dicts because Pydantic isn't available there.
- Root `mcp-helper.sh` and `generate-config.sh` are one-line `exec` wrappers around `scripts/`.
- `docs/` is extensive and largely aspirational — verify claims against code before relying on them.
