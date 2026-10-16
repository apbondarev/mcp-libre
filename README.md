# LibreOffice MCP Server

A Model Context Protocol server that runs **inside LibreOffice** as an extension. It gives an AI assistant 113 tools over the live documents you have open — reading and rewriting text run by run, comments, tracked changes, fields, tables, pictures, styles — through UNO, with no file round-trip and no second LibreOffice process.

[![LibreOffice](https://img.shields.io/badge/LibreOffice-24.2+-green.svg)](https://www.libreoffice.org/)
[![MCP Protocol](https://img.shields.io/badge/MCP-2024--11--05-orange.svg)](https://spec.modelcontextprotocol.io/)
[![Python](https://img.shields.io/badge/python-3.12+_(tests)-blue.svg)](https://www.python.org/downloads/)

## How it works

The server is a LibreOffice extension. Start it from **Tools > MCP Server** and it serves MCP over SSE on `http://localhost:8765/sse`; any MCP client that speaks SSE can connect to that URL. Because it runs in the office process, a tool call acts on the document in front of you: what it writes appears in the window as it happens, and every mutation is one Ctrl+Z.

Two constraints shape all of it, and they are requirements rather than preferences:

- **Standard library only.** The code runs under LibreOffice's bundled Python, so the SSE transport and the JSON-RPC layer are hand-rolled — no `mcp`, no `pydantic`, no `httpx`.
- **No external programs.** A tool works with nothing but LibreOffice and the standard library, on any machine where LibreOffice runs. Anything that would need ImageMagick, `pdftoppm`, `gs` or a screenshot utility is either reachable through UNO (a filter, `XRenderable`, `GraphicProvider`) or is not offered at all.

## Requirements

- **LibreOffice 24.2+**
- **Python 3.12+ and uv** — for the test suite only; the extension itself needs neither

```bash
./mcp-helper.sh requirements   # installation instructions per platform
./mcp-helper.sh check          # verify LibreOffice / Python / uv
```

## Installation

```bash
cd plugin
./install.sh install    # build the .oxt and register it with unopkg
```

Then **restart LibreOffice** and start the server from **Tools > MCP Server**.

```bash
./install.sh status      # unopkg list + curl localhost:8765/health
./install.sh test        # exercise the HTTP API (needs a running soffice)
./install.sh build       # package only — ./build.sh writes build/libreoffice-mcp-extension-1.0.0.oxt
./install.sh uninstall   # remove the extension
```

`/tmp/mcp_extension.log` is the only usable log — UNO swallows stderr. Set `MCP_EXTENSION_LOG` to point it elsewhere.

### Without the extension

If LibreOffice refuses to register a Python component (`unopkg add` failing with "C++ code threw St9bad_alloc"), `scripts/mcp_standalone.py` serves the same tools on the same port over a UNO socket, so a client configured for `http://localhost:8765/sse` keeps working unchanged:

```bash
soffice --writer --norestore \
    "--accept=socket,host=127.0.0.1,port=2002;urp;" ~/Documents/file.odt &

/usr/bin/python3 scripts/mcp_standalone.py    # the system Python, which has python3-uno
```

It serialises tool calls: pyuno multiplexes one socket bridge, and concurrent calls over it interleave and return each other's state.

## Tools

113 tools, registered by subject. Every tool that acts on a document takes a `document` parameter, so a call cannot wander to whatever window happens to be focused.

| Area | Tools |
|------|-------|
| Documents | `create_document_live`, `open_document_live`, `get_document_info_live`, `save_document_live`, `close_document_live`, `rename_document_live`, `export_document_live`, `list_open_documents` |
| Reading | `read_paragraphs_live`, `get_text_content_live`, `get_outline_live`, `find_text_live`, `get_cursor_info_live`, `select_live` |
| Text | `insert_text_live`, `replace_range_live`, `replace_selection_live`, `read_runs_live`, `replace_runs_live`, `set_language_live`, `check_spelling_live` |
| Formatting | `format_text_live`, `format_range_live`, `format_ranges_live`, `format_paragraph_live`, `apply_paragraph_style_live`, `list_styles_live`, `describe_style_live` |
| Styles | `create_style_live`, `update_style_live`, `rename_style_live`, `delete_style_live`, `replace_style_live`, `find_by_style_live`, `get_direct_formatting_live`, `clear_direct_formatting_live` |
| Comments | `list_comments_live`, `add_comment_live`, `update_comment_live`, `delete_comment_live`, `resolve_comments_live`, `set_comment_language_live` |
| Tracked changes | `list_tracked_changes_live`, `accept_tracked_changes_live`, `reject_tracked_changes_live` |
| Tables | `create_table_live`, `read_table_live`, `list_tables_live`, `describe_table_live`, `format_table_live`, `delete_table_live`, plus row/column insert and delete, `merge_table_cells_live`, `split_table_cells_live`, `sort_table_live` |
| Objects that are not text | fields, formulas, images, bookmarks, hyperlinks, anchors — `list_fields_live`, `insert_field_live`, `list_formulas_live`, `add_formula_live`, `list_images_live`, `export_image_live`, `render_page_live`, and the rest beside them |
| Structure | sections, headers and footers, page layout, indexes, cross-references and captions |
| Paragraph surgery | `split_paragraph_live`, `merge_paragraphs_live`, `move_paragraph_live`, `copy_paragraphs_live` |
| History | `list_undo_steps_live`, `undo_live`, `redo_live` |
| Batching | `batch_live` — a list of steps applied as **one** undo step |

`tools/list` over the SSE connection is the authoritative list, with each tool's JSON Schema. The client is also handed `mcp_guide.INSTRUCTIONS` in the `initialize` result: the address forms, the refusals and the way round each, what is not text, and the language rule. The same text is [`skills/libreoffice-mcp/SKILL.md`](skills/libreoffice-mcp/SKILL.md), written by `scripts/write_skill.py` from that one constant, so the two cannot drift.

### What the server refuses, and why

Rewriting a stretch of a Writer document is destructive in ways that are invisible in the resulting text. A range holding several runs loses inline code, italics and links when it is replaced by one string; a comment, a picture, a field or a recorded change inside it is destroyed outright — and a destroyed deletion comes back as live text. So the rewriting tools **refuse** by default and say what would be lost:

- `read_runs_live` reports the runs with their formatting, and the comments, images, fields and tracked changes that travel on them;
- `replace_runs_live` writes runs back with their properties, keeping what did not change and restoring what a flat replacement would have cost;
- `replace_range_live` and `replace_selection_live` refuse a multi-run range or any hyperlink unless `flatten=true` accepts the loss, and then report it (`fields_dropped`, `images_dropped`, `changes_dropped`);
- `format_ranges_live` takes a list of spans and applies them in one guarded edit, because syntax colouring a code block is twenty spans, not twenty calls and twenty undo steps.

Every refusal carries a machine-readable `code` from a closed set (`NO_DOCUMENT`, `READ_ONLY`, `INVALID_ADDRESS`, `NOT_FOUND`, `WOULD_LOSE_FORMATTING`, `UNSUPPORTED`, `FAILED` and the rest), and every result carries `elapsed_ms`. Mutations go through one guard: read-only refusal, one undo context, a three-state `track_changes` contract, and the document's own recording setting restored afterwards.

## Repository layout

```
plugin/
├── pythonpath/          the extension: 60 modules
│   ├── registration.py  the UNO component, menu dispatch, server lifecycle
│   ├── ai_interface.py  SSE transport + hand-written JSON-RPC
│   ├── mcp_server.py    tool registry and dispatch
│   ├── mcp_guide.py     the instructions sent to a client on initialize
│   ├── uno_bridge.py    UNOBridge, assembled from one mixin per subject
│   ├── uno_<subject>.py the bridge's parts — the only code touching UNO objects
│   └── mcp_<subject>_tools.py  the tools, beside their subject
├── install.sh, build.sh, test_plugin.py
└── *.xcu, description.xml, META-INF/manifest.xml
scripts/
├── mcp_standalone.py    the same tools over a UNO socket
├── write_skill.py       regenerate the skill from mcp_guide.py
└── mcp-helper.sh        prerequisite checks (root mcp-helper.sh wraps it)
skills/libreoffice-mcp/  SKILL.md, generated — do not edit by hand
tests/                   asserting tests against faked UNO modules
docs/                    design notes and a survey of other servers
CLAUDE.md                the measured behaviour of Writer, as found by building these tools
```

## Testing

```bash
uv sync
uv run pytest tests/ -q      # 813 tests, none of them needing LibreOffice
```

`tests/uno_stubs.py` fakes the `uno` and `com.sun.star.*` modules, which makes `plugin/pythonpath/` importable in a plain venv. `tests/fake_writer.py` and its `fakes_*` companions model Writer's behaviour — including the parts that are easy to get wrong, such as an enum that stringifies as `<Enum instance com.sun.star.awt.FontSlant ('ITALIC')>`. `tests/live/` drives a real office over the SSE server and is not collected by pytest; keep it sequential.

`tests/test_guide.py` fails when the checked-in skill is stale, when the guide names a tool the server does not register, or when `initialize` stops carrying it. `tests/test_result_contract.py` fails if an error code appears in the source that `ERROR_CODES` does not hold, or if any tool answers a refusal without one. `tests/test_schemas_match_handlers.py` holds the JSON Schema to the handler's own signature.

There is no linter or formatter configured.

## Documentation

- **[CLAUDE.md](CLAUDE.md)** — how Writer actually behaves under UNO, measured while building these tools. The load-bearing document in this repository.
- **[plugin/README.md](plugin/README.md)** — extension guide
- **[docs/LIBREOFFICE_MCP_EXTENSION.md](docs/LIBREOFFICE_MCP_EXTENSION.md)** — extension reference
- **[docs/LIBREOFFICE_PLUGIN_DESIGN.md](docs/LIBREOFFICE_PLUGIN_DESIGN.md)** — the architecture design
- **[docs/PREREQUISITES.md](docs/PREREQUISITES.md)** — system requirements
- **[docs/OTHER_MCP_SERVERS.md](docs/OTHER_MCP_SERVERS.md)** — a survey of comparable servers and what is worth taking from them

## Security

Everything runs locally. The server binds to `localhost` and talks to no network; it can reach exactly the documents LibreOffice has open and the files those documents name.

## License

MIT — see [LICENSE](LICENSE). Other options are discussed in [docs/LICENSE_OPTIONS.md](docs/LICENSE_OPTIONS.md).
