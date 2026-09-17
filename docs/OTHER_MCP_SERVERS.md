# Other office MCP servers, and what this one should take from them

Surveyed 2026-09-15. Everything said here about another project comes from its own
README or its source on GitHub, read but **not run** — treat their numbers (tool
counts, benchmarks, "live-verified") as their claims, not as measurements made here.
Everything said about *this* server was checked against the working tree.

The point of the survey is not the tool counts. It is the three or four interface
decisions other people made differently, each of which costs us something today.

---

## 1. The servers

### 1.1 kittrellbj/mcp-libre — our own fork, gone wide

A fork of **this repository**, taken at the 32-tool state, now at ~398 registered
tools (393 live, 5 stub) across Writer (131), Calc (99), Impress (41), Draw (16) and
116 shared-service tools. Same architecture as ours — a `.oxt` extension running
inside LibreOffice, an embedded HTTP server on `localhost:8765`, a `mcp:` protocol
handler for the Tools menu — so every difference is a decision, not an accident of
a different platform. This is the most useful comparison in the survey.

Interface:

- Tools are declared with a decorator (`@register_tool(name=..., priority=..., purpose=..., parameters=schema({...}), status=...)`) in `tools/*.py`, split by subject, registered into one registry. We do the same split by hand in `mcp_*_tools.py`.
- Every tool answers the **same envelope**: `{success, result, warnings, error: {code, message, details}, document_id, elapsed_ms}`, with a closed set of error codes (`NO_ACTIVE_DOCUMENT`, `WRONG_DOCUMENT_TYPE`, `OBJECT_NOT_FOUND`, `AMBIGUOUS_SELECTOR`, `UNSUPPORTED_CAPABILITY`, `INVALID_RANGE`, `INVALID_PARAMETER`, `INVALID_STATE`, `FILE_EXISTS`, `PERMISSION_DENIED`, `UNO_EXCEPTION`, `TIMEOUT`, `SECURITY_POLICY_DENIED`, `NOT_IMPLEMENTED`). `build_error` raises on an unknown code, so the set cannot rot.
- Addressing is by **paragraph index, cursor position, selection, or search hit** — `goto_paragraph_live`, `goto_position_live`, `select_paragraph_live`, `select_text_range_live` (0-based character range), then act on the selection. There is no address object; the document's own cursor is the address.
- Documents are addressed by a `document_id` from a registry, not only "the active one".
- Tool discovery is itself a tool: `list_tools_live`, `get_tool_schema_live`, `validate_tool_call_live`, `set_tool_profile_live`, `get_capabilities_live` — with **profiles** derived from the active document's type, so a Writer session does not carry 99 Calc schemas in the model's context.
- `batch_execute_live(operations, stop_on_error, undo_label)` plus explicit `begin_undo_context_live` / `end_undo_context_live` / `cancel_undo_context_live`.
- Diagnostics as tools: `ping_live`, `get_server_info_live`, `get_diagnostics_live`, `get_recent_errors_live`, `get_session_state_live`, `get_document_snapshot_live`.

Writer capabilities we do not have at all: track-changes **review** (`get_tracked_changes`, `accept_tracked_change`, `reject_tracked_change`, `accept_all`, `reject_all`), `undo_live`/`redo_live`, fields (`list_fields`, `insert_date_time_field`, `insert_page_number_field`, `update_fields`, `delete_field`), bookmarks (`add`/`goto`/`list`/`rename`/`delete`), cross-references and captions, TOC and indexes (`insert_toc`, `insert_alphabetical_index`, `add_index_mark`, `update_index`), footnotes and endnotes, sections, headers and footers, page layout / page styles / page breaks / columns / line numbering, hyperlink CRUD as a subject (`list_hyperlinks`, `insert_hyperlink`, `update_hyperlink`, `remove_hyperlink`), table row/column insert and delete, cell merge and split, `sort_table`, `convert_text_to_table` / `convert_table_to_text`, paragraph surgery (`split`, `merge`, `move`, `copy`), `find_regex` / `replace_regex` / `find_by_style`, direct formatting (`get_direct_formatting`, `clear_direct_formatting`, `copy_formatting`), styles CRUD (`create_style`, `clone_style`, `update_style`, `rename_style`, `delete_style`, `replace_style`), document properties and statistics, `lock_document_updates` / `unlock_document_updates`, document events (`get_document_events`, `wait_for_document_event`), `print_document`, `list_export_filters`, `list_fonts`, `set_zoom`, `goto_page`, `get_view_state`.

Two things in it are engineering, not surface:

- **A process-wide UNO execution lock** (`_UNO_EXECUTION_LOCK`) around the whole tool-execution sequence, plus a bounded admission semaphore (`MAX_CONCURRENT_TOOL_CALLS = 4`, `ADMISSION_TIMEOUT_SECONDS = 30`) that answers `503` with `Retry-After` instead of queueing without limit. Their write-up says the pyuno **bridge proxy itself** corrupts under overlapping calls — not a per-document data race — and that a finer per-document lock still left 95/600 calls failing, because it did not cover object *resolution*. They report 600/600 clean afterwards.
- **Host/Origin trust** (`host_trust.py`): `Host` and `Origin` are parsed and matched against `{localhost, 127.0.0.1, ::1}`; CORS echoes back only a trusted origin, never `*`. The reason given is DNS rebinding — a page on an attacker's domain that resolves to `127.0.0.1`.

They also keep an honest stub policy: a tool that cannot be implemented returns `NOT_IMPLEMENTED`, is off unless `MCP_LIBRE_ENABLE_SCAFFOLD_STUBS=1`, and the README records *why* it is blocked (e.g. `ChapterNumberingRules.replaceByIndex()` refusing the sequence `getByIndex()` just returned; `XSlideShowController` always `None` headless).

### 1.2 SecurityRonin/docx-mcp — file-based OOXML surgery, review-shaped

200+ tools over `.docx` files: unzip, edit the XML, rezip. No LibreOffice at all. The
whole interface is shaped around **review**, which is the part worth reading:

- Every editing tool takes `tracked` (default **true**) and an `author`; insertions and deletions come out as real `w:ins`/`w:del`.
- `generate_change_summary` writes a numbered plain-text change log of the session ("1. REPLACEMENT by Claude on … Deleted: '30 days' Inserted: '60 days'"), and `compare_documents` / `diff_to_text` produce the same thing from two files that nobody edited through the server.
- Comments are **threads**: `add_comment`, `reply_to_comment`, `get_comments`.
- `audit_document`, `validate_footnotes`, `validate_endnotes`, `validate_paraids` — a structural check before delivery, and every edit is validated against OOXML before saving, "catching … orphaned footnotes, duplicate internal IDs, and broken cross-references that would otherwise cause Word to 'repair' … your document".
- `set_document_protection` (tracked-changes-only, read-only, comments-only, with a password).
- It **ships a Skill with the server**: "A companion skill auto-installs the first time the server starts, teaching Claude the document editing workflow, OOXML pitfalls, and audit checklist. It auto-updates on every upgrade."

### 1.3 knorq-ai/docx-mcp-server — 40 tools, batch-shaped, with stable anchors

Also `.docx`-over-XML. Two ideas stand out.

**Stable anchors.** Their own words: "Paragraphs are normally addressed by integer
block index, but every insert/delete shifts the indices of later blocks — so a
multi-step edit has to re-read after each change. Anchors fix this: an anchor is a
stable id (Word's `w14:paraId`) that stays attached to its paragraph across index
shifts." `ensure_anchors` assigns them and returns the index→anchor map (idempotent);
`search_text` returns an `anchor` per hit; every edit tool takes `anchor`/`anchors`
instead of an index, and returns the anchors of paragraphs it touched or created, so
a pipeline never re-reads. Anchors do not cover paragraphs inside tables (v1).

**Plural by default.** `replace_texts`, `edit_paragraphs`, `insert_paragraphs`,
`delete_paragraphs`, `set_paragraph_formats`, `add_comments`, `edit_table_cells` —
the singular is the special case. Overlap between batch items is *refused* with the
reason given (tracked `w:ins`/`w:del` nesting does not round-trip through a reject),
and the ordering trap is documented (several inserts at one position land reversed).

They also argue the case for tools over generated code in tokens: 65–95% fewer output
tokens than the agent writing `python-docx`, break-even at 3–5 operations — the same
conclusion we reached from the other end, when a slow `find_text` sent an agent off to
write its own UNO script and the script segfaulted.

### 1.4 jwingnut/libreoffice-mcp-ubuntu — nine tools, one `action` each

A FastMCP **stdio** server that talks HTTP to an extension like ours. Its whole
Writer surface is nine tools, each with an `action` enum: `document(action=…)`,
`structure`, `cursor`, `selection`, `search`, `track_changes`, `comments`, `save`,
`text`. Two lessons: a stdio front end makes an in-process extension usable by clients
that do not speak SSE, and consolidating verbs under one tool is a real way to spend
less of the model's context on schemas (at the cost of per-action schema clarity).

### 1.5 WaterPistolAI/libreoffice-mcp — UNO through OooDev, all four applications

Connects to a headless LibreOffice over a socket (port 2083) and uses **OooDev**
rather than raw UNO. Covers Writer, Calc, Impress, Draw and part of Base: charts,
pivot tables, conditional formatting, sorting, statistics, form controls,
`run_macro`. Relevant to us only as evidence for what a Pythonic layer buys — and as
a reminder that `run_macro` is an escape hatch we have deliberately not offered.

### 1.6 Jhanz111/libreoffice-containerized-mcp-server — 15 tools, templates

LibreOffice in a container, driven as files. Its distinctive surface is a **template
system**: turn a document into a template, `{{mustache}}` / `%percent%` / `$dollar`
placeholders, a searchable template library with metadata, then generate documents
from it. Also document comparison, merging, splitting, style transfer between
documents, summarisation.

### 1.7 The thin ones

`harshithb3304/libre-office-mcp`, `patrup/mcp-libre`, `jwingnut/mcp-libre` and the
various forks of this repository are file- or helper-process-oriented and offer less
than our external `src/libremcp.py`. `chfle/word-to-pdf-mcp` is a single conversion
service built on **unoserver** — worth remembering as the standard way to keep a
LibreOffice process warm outside the GUI.

---

## 2. Where we stand

| | this server | the fork | docx-mcp | knorq | ubuntu |
|---|---|---|---|---|---|
| runs inside LibreOffice | yes | yes | — | — | via extension |
| tools | 45 | ~398 | 200+ | 40 | 9 (× actions) |
| addressing | paragraph / block / range / cell / selection **+ anchor** | cursor + index | paragraph id | index **+ stable anchor** | index |
| refuses a lossy write | **yes** | no | no | partly (batch overlap) | no |
| runs, links, comments, pictures survive a rewrite | **yes** | no | n/a | n/a | no |
| comments | list/add/update/delete, language, ids | list/add/update/delete/resolve | + **threads** | + threads | list/add |
| track changes | record-or-not, three states | + accept/reject | + accept/reject by author, change log | + accept/reject all | + accept/reject |
| tables | read/describe/format/create/delete | + rows/cols/merge/sort/convert | + rows | + cells | — |
| page image | **render_page** | — | — | — | — |
| undo | one step per call | + explicit contexts, undo/redo | — | — | — |
| concurrency | none (threaded server) | process lock + admission | n/a | n/a | n/a |
| Origin/Host check | **no** (`ACAO: *`) | yes | n/a | n/a | n/a |
| transport | HTTP+SSE, `2024-11-05` | SSE + JSON-RPC, session/version negotiation | stdio | stdio | stdio → HTTP |

What nobody else in the survey has, and we should not lose while copying from them:

- **Refusing a write that would destroy something.** `replace_range`/`replace_runs` count runs, hyperlinks, comments, inline pictures and tables inside the range and refuse unless `flatten=true`. Every other server writes the string and lets the user find out.
- **Comments as first-class**: stable ids, the anchoring rule (`AnnotationEnd` swallowed by a rewrite that starts where the anchor ends), the language-comes-from-the-"Comment"-style finding, comments carried through a rewrite rather than re-created.
- **Pictures visible to text tools** (empty `Frame` portions), and the measured rule for which rewrite destroys an inline one.
- **`render_page`** — the page as the user sees it, through LibreOffice alone, no external program. Nobody else renders anything.
- **The address matrix**, including table cells and a live selection, measured per tool.
- **Two-layer testing**: faithful fakes for unit tests plus a live harness against a real headless LibreOffice, which is what caught the italic enum, the comment anchor, the cell identification and the address drift.

---

## 3. What to add here, in order

### 3.1 Session anchors — **done**, and what it turned out to be

Our addresses are indices, and indices move. Our own note for the GraphQL document
told an agent to walk blocks **from the last to the first** so that a `create_table`
with `replace: true` would not invalidate the indices it collected — that instruction
existed only because the interface had no stable handle.

UNO turned out to give a better primitive than Word's `paraId`: a text cursor is kept
by the document and moves with the text. `anchor` hands out a token for one,
`{"anchor": "a7f3c1"}` is an address anywhere an address is taken, and `find_text` and
`read_paragraphs` hand one back per hit or paragraph with `anchors: true`.
`list_anchors` says where each points now, `drop_anchors` lets them go.

The open questions in the first draft of this section were measured rather than
guessed, and the answers shaped the tool:

- A held cursor keeps its text when a paragraph above it is removed or inserted, and
  grows when text is inserted inside it.
- The cursor that performs the rewrite **keeps the new text**, so a replacement made
  *through* an anchor leaves that anchor pointing at the replacement. Any other cursor
  over the same stretch collapses to an empty position — without throwing, and
  indistinguishable from a caret by asking. So an anchor records what it covered when
  it was made, and a stale one is refused by name rather than resolved to the hole it
  left.
- A cursor into a cell whose table is removed, or into a closed document, throws
  `SwXTextCursor: disposed or invalid` — caught, and reported as "is gone".
- Documents are told apart by `RuntimeUID`, which is what makes an anchor refuse a
  document it was not made in.
- A **bookmark** moves the same way and is saved in the file, so it is the answer when
  a handle must outlive the session — at the cost of showing up in the user's
  Navigator. Anchors deliberately do not.

### 3.2 Origin/Host validation, and no `Access-Control-Allow-Origin: *`

`plugin/pythonpath/ai_interface.py:215` sends `Access-Control-Allow-Origin: *` and
nothing checks `Host` or `Origin`. The server binds `localhost`, which stops a remote
socket but **not a browser**: any page the user visits can drive every tool on
`127.0.0.1:8765` — read their open document, rewrite it, export it to disk. The fork
already solved this in 30 lines (`host_trust.py`); the MCP specification asks local
HTTP servers to validate `Origin` for exactly this reason. This is the cheapest
item on the list and the only one that is a defect rather than a feature.

### 3.3 A UNO execution lock, after re-measuring our claim

`CLAUDE.md` currently says concurrent calls are only a problem for the out-of-process
test rig, and that "in-process concurrent tool calls behave correctly". Our HTTP
server is a `ThreadingTCPServer` with `daemon_threads = True`, so two tool calls do
run on two threads. The fork reports that pyuno's bridge proxy itself corrupts under
overlapping calls, and that they needed a lock around **object resolution as well as
mutation** to get a clean run. Either their finding does not apply to us and we
should say why, or our claim is wrong and a process-wide lock plus bounded admission
belongs in `ai_interface`. This needs a live burst test, not an opinion.

### 3.4 Hand the agent the manual — **done**

`CLAUDE.md` holds everything measured about Writer through UNO, and an agent using
the server over MCP saw none of it. Both routes are now taken, from one source:

- `mcp_guide.INSTRUCTIONS` goes out as the `instructions` of the MCP `initialize`
  result — 4058 characters, about 650 words: the five address forms, what the
  refusals mean and the way round each, that comments and pictures are not text,
  the language rule, tables, batching, and which calls are cheap.
- `scripts/write_skill.py` writes the same text into `skills/libreoffice-mcp/SKILL.md`
  as a Claude Code skill, so a client that does not show `instructions` still gets it.
  `tests/test_guide.py` fails if the checked-in skill is stale, if the guide names a
  tool this server does not register, or if `initialize` stops carrying it.

Unlike docx-mcp, the skill is **not** auto-installed into the user's
`~/.claude/skills` when the server starts: that directory is theirs, and on this
machine the name `libreoffice-writer` was already taken by a skill about driving
LibreOffice from the command line — which is why the skill here is called
`libreoffice-mcp`. Installing it is one `cp -r`, recorded in CLAUDE.md.

### 3.5 Review tools for tracked changes

We honour the three-state recording contract, and then leave the user in front of a
document full of redlines with no way to read or resolve them through the server:
`list_tracked_changes` (author, date, kind, text, address), `accept`/`reject` one,
`accept_all`/`reject_all`, optionally filtered by author as docx-mcp does. The
redlines are already reachable — `uno_documents.py:166` counts them with
`doc.getRedlines()`.

### 3.6 A change log for the session

docx-mcp's `generate_change_summary` is the tool a reviewer actually wants: not "the
call succeeded" but "here is every insertion, deletion and replacement, numbered".
We have richer material than they do (`runs_kept`, `runs_rewritten`, `comments_kept`,
`comments_written` are already in our results) — what is missing is somewhere to
accumulate them and one tool to print them.

### 3.7 Batch — **done**; undo across separate calls is not

`format_ranges` proved the shape, and `batch_live` now generalises it: a list of
`{tool, parameters}` steps, run in order inside one undo context, so a plan an
assistant carries out is one edit to the reader instead of twelve. Measured on the
real translation: 33 requests and 6.61s became 22 and 4.30s, and twelve entries in
the Undo menu became one.

What the measurements added to the design:

- Undo contexts nest and only the outermost becomes an entry; a context that wrote
  nothing leaves none. So a batch that fails before writing cannot be "undone" — a
  bare `undo()` there would take back the reader's own last action.
- Counting entries does not say whether a batch wrote: Writer's undo stack has a
  limit, and on a full one a new entry pushes the oldest out without the count
  moving. `undo_group` watches the top of the stack too — a real bug, caught by the
  live harness on a long session.
- `on_error` is `stop` (keep what was done and report), `continue`, or `undo` (take
  the whole batch back), rather than the fork's never-roll-back.
- The fork's warning is carried over: a batch holds the server for as long as it
  runs and no step can be timed out on its own, so it is capped at 50 steps.

A step cannot read an earlier step's result, which keeps batching to plans already
worked out. Explicit `begin_undo_context` / `end_undo_context` **across** calls is
deliberately not built: a context left open by a client that went away would swallow
the reader's own later edits into an assistant's undo step.

### 3.8 Comment threads

Writer supports replies to a comment; our `list_comments` reports a flat list. Both
docx servers model threads, because that is how review conversations actually look.
Needs a measurement first: how a reply is represented in UNO (`ParentName` on the
annotation) and whether it survives our rewrite path.

### 3.9 Document structure: the honest gap list

Everything a real Writer document has that we cannot touch: **fields** (date, page
number, document property, and `update_fields`), **bookmarks**, **cross-references**
and captions, **TOC/indexes**, **footnotes/endnotes**, **sections**, **headers and
footers**, **page layout** (size, margins, orientation, breaks, columns). Any one of
these turns "format this document properly" into a refusal today. They are ordinary
UNO work; the reason they are missing is that nothing asked for them yet.

Alongside them, the smaller ones: table rows/columns insert and delete, cell merge
and split, `sort_table`; `list_hyperlinks` and `remove_hyperlink` (we can already
*set* a link through `format_range`'s `link` property); paragraph split/merge/move;
`find_by_style`; `get_direct_formatting` / `clear_direct_formatting`; style CRUD
beyond `describe_style`; `undo`/`redo`.

### 3.10 Error codes and `elapsed_ms` — **done**

Ours were plain dicts with `success` and a free-text `error`, so a caller could
only match on English. Every refusal now carries a `code` from a closed set of
nine — `NO_DOCUMENT`, `WRONG_DOCUMENT_TYPE`, `READ_ONLY`, `INVALID_ADDRESS`,
`NOT_FOUND`, `INVALID_PARAMETER`, `WOULD_LOSE_FORMATTING`, `UNSUPPORTED`,
`FAILED` — and every result carries `elapsed_ms`.

Done without the rewrite the first draft of this section feared. Two of the
refusals mean the same thing everywhere and became one call each
(`refusal("INVALID_ADDRESS", e)` for a caught `AddressError`, `FAILED` for a
caught `Exception`); the rest were stamped where they stand, so the messages and
their extra keys are untouched and the diff reads as one added key per site. The
stamps themselves live in `_run_tool`, the single place every call goes through —
which is also why a batch's steps get them.

Unlike the fork's envelope this is not a wrapper: the payload stays where it was,
so nothing that read a result before has to change. What holds it together is
`tests/test_result_contract.py` — it fails if a code turns up that `ERROR_CODES`
does not declare, and if any of the 45 tools answers a refusal without one.

Smaller than the fork's fourteen codes on purpose: a code earns its place only
when a caller would do something different about it. `AMBIGUOUS_SELECTOR`,
`FILE_EXISTS`, `TIMEOUT` and the rest are `INVALID_PARAMETER` or `FAILED` here
until something needs to tell them apart.

### 3.11 Transport: we are two revisions behind

We implement HTTP+SSE with `protocolVersion: "2024-11-05"` (`ai_interface.py:53`).
That transport was replaced by Streamable HTTP in `2025-03-26` and is classified
**Deprecated** in the current `2026-07-28` specification, which also drops the GET
stream and protocol-level sessions and adds `Mcp-Method` / `Mcp-Name` headers.
Clients still accept the old transport today; this is a "before it breaks" item, and
it pairs naturally with a small **stdio bridge** (as jwingnut's Ubuntu server has)
for clients that do not speak HTTP to a local port at all.

### 3.12 If the tool count grows: profiles and discovery

Forty-one tools with long, honest descriptions already cost a noticeable slice of the
model's context. Before adding thirty more, take the fork's idea: `list_tools`,
`get_tool_schema`, and **profiles** keyed to the active document's type, so a Writer
session never carries Calc schemas. The Ubuntu server's `action`-dispatch
consolidation is the blunter version of the same economy.

### 3.13 Ideas worth stealing, unranked

- **Templates with placeholders** (Jhanz111): a real workflow for generating documents from a house style.
- **`compare_documents`** as a tool, not just a session log — LibreOffice has document comparison built in.
- **Structural audit** before delivery (docx-mcp): broken references, orphaned notes, empty headings.
- **Document protection** (read-only / comments-only) — LibreOffice supports it.
- **`lock_document_updates`** around a long batch: LibreOffice's own screen-update freeze, which is free speed.
- **Document events** (`wait_for_document_event`): the fork's caveat is instructive — with one process-wide lock, waiting for an event that another tool call would raise deadlocks by construction.
- **`unoserver`** as the model for a warm headless process, if the external `src/libremcp.py` server is ever taken seriously again.

---

## Sources

- [kittrellbj/mcp-libre](https://github.com/kittrellbj/mcp-libre) — fork of this repository; README, `plugin/pythonpath/tools/*`, `ai_interface.py`, `host_trust.py`
- [SecurityRonin/docx-mcp](https://github.com/SecurityRonin/docx-mcp)
- [knorq-ai/docx-mcp-server](https://github.com/knorq-ai/docx-mcp-server)
- [jwingnut/libreoffice-mcp-ubuntu](https://github.com/jwingnut/libreoffice-mcp-ubuntu)
- [WaterPistolAI/libreoffice-mcp](https://github.com/WaterPistolAI/libreoffice-mcp)
- [Jhanz111/libreoffice-containerized-mcp-server](https://github.com/Jhanz111/libreoffice-containerized-mcp-server)
- [harshithb3304/libre-office-mcp](https://github.com/harshithb3304/libre-office-mcp), [jwingnut/mcp-libre](https://github.com/jwingnut/mcp-libre), [patrup/mcp-libre](https://github.com/patrup/mcp-libre)
- [GongRzhe/Office-Word-MCP-Server](https://github.com/GongRzhe/Office-Word-MCP-Server)
- [chfle/word-to-pdf-mcp](https://github.com/chfle/word-to-pdf-mcp) (unoserver)
- [MCP specification 2026-07-28](https://blog.modelcontextprotocol.io/posts/2026-07-28/), [Streamable HTTP](https://modelcontextprotocol.io/specification/draft/basic/transports/streamable-http)
