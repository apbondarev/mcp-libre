# Writer text tools for the MCP plugin

Design, 2026-08-18.

## Purpose

Give an AI assistant enough of a tool surface to read and edit text in an open
LibreOffice Writer document: find a passage, understand the document's shape
without reading all of it, and change a specific piece of text reversibly.

In scope: body text — reading, searching, replacing, deleting, inserting
paragraphs, paragraph and character formatting, and the safety mechanics that
make edits reviewable.

Out of scope: table cell *content*, images, footnotes, fields, tables of
contents, style authoring, and the external file-based server in
`src/libremcp.py`, which has no access to a live view and cannot participate.

## Current state

The plugin exposes nine tools. For text work the relevant ones are
`get_text_content_live` (whole document), `insert_text_live`,
`format_text_live`, and `get_cursor_info_live`.

Three facts about that surface drive this design:

1. **There is no single way to address text.** `insert_text` takes an absolute
   character offset and reaches it with `gotoStart` + `goRight(position)`;
   `format_text` acts only on the human's current selection;
   `get_cursor_info` reports `paragraph_index` + `offset_in_paragraph`. Nothing
   else can point at text at all. Every tool added without settling this will
   invent a fourth scheme.

2. **There is no search and no replace.** The only way to locate text is to
   pull the entire document into the model's context, which does not scale past
   a few pages, and there is no way to change what was found.

3. **`format_text_live` cannot be driven by an assistant, and fails silently.**
   It formats `selection.getByIndex(0)`, so it works only if a human selected
   something first. Its guard `selection.getCount() == 0` never fires: a
   collapsed caret yields `range_count: 1` with an empty string (verified on
   LibreOffice 24.2.7.2), so an empty selection reports
   `"Formatting applied successfully"` while changing nothing.

Two further facts, established while building `get_cursor_info_live`, constrain
any implementation:

- A text range belongs to the text object that owns it. Inside a table cell or
  a frame that is not `doc.getText()`, and passing a foreign range to the body
  text throws *"End of content node doesn't have the proper start node"*.
- `isinstance` is useless on UNO proxies; type checks go through
  `_supports(obj, service)` in `uno_bridge.py`.

## Core decision: one address type

Every tool that reads or changes a specific piece of text takes an **address**,
a JSON object in exactly one of these forms:

| Form | Meaning |
|---|---|
| `{"paragraph": N, "offset": K, "length": L}` | `L` characters from offset `K` in body paragraph `N`. `offset` defaults to 0, `length` omitted means to the end of the paragraph. |
| `{"selection": true}` | Whatever the human currently has selected. |
| `{"bookmark": "name"}` | A named bookmark, for edits that must survive earlier edits. Phase 4. |

Paragraph indices are 0-based, count only paragraphs in the body enumeration,
and skip tables — the same convention `get_cursor_info_live` already reports, so
its output can be fed straight back in.

One resolver, `_resolve_address(doc, address) -> XTextRange`, is the only place
that turns an address into a range. It is the single most heavily tested unit
in this work, because every mutating tool depends on it.

**Consequences accepted:**

- A `{"paragraph": …}` address cannot point inside a table cell or a frame,
  because those paragraphs are not in the body enumeration; the resolver
  rejects such an address with a clear error rather than guessing.
  A `{"selection": true}` address has no such limit — it resolves to whatever
  range the human selected, wherever that lives — so editing inside a cell
  stays possible as long as the human points at it. Tools must therefore treat
  the two forms as equally valid inputs and never assume a paragraph index is
  available.
- **Indices shift when the document changes.** A mutation invalidates any
  address after the edit point. Mitigation, in order of cost: every mutating
  tool returns the affected paragraph's new index and the document's new
  paragraph count, so the assistant can re-anchor; and for a genuinely
  multi-step edit, bookmarks (phase 4) give stable handles. This design does
  not attempt to make plain indices stable — it makes the drift visible.

## Tools

The phases are deliberately shippable in order, and each one gets its own
implementation plan rather than all sixteen tools landing as a single change.
Phase 1 is useful on its own; phase 2 depends on the address resolver phase 1
introduces.

### Phase 1 — see the document

| Tool | Returns | Mechanism |
|---|---|---|
| `get_outline_live` | Headings as `{paragraph, level, text}`, at most 200, with `truncated` when the document has more | walk paragraphs, read `OutlineLevel` / `ParaStyleName` |
| `read_paragraphs_live(start, count)` | `{paragraph, text, style, truncated}` per paragraph, plus `total_paragraphs`. `count` defaults to 50 and is capped at 200 | `createEnumeration`, already proven |
| `find_text_live(query, regex, max_results)` | Hits as `{address, paragraph, offset, length, context}`. `regex` defaults to false, `max_results` to 50 | `createSearchDescriptor` + `findAll` |

`get_outline_live` is the highest-value tool here: it turns a 100-page document
into a few dozen lines the model can hold, with addresses to drill into.

### Phase 2 — change the text

Every tool in this phase is a mutation and therefore carries the whole
cross-cutting contract below (undo grouping, read-only guard, address
resolution, new-index reporting).

| Tool | Purpose | Mechanism |
|---|---|---|
| `replace_selection_live(text)` | "Rewrite what I selected" — the most common request, currently impossible | `insertString(view_cursor, text, True)`; the `bAbsorb=True` flag replaces the selection, where `insert_text` passes `False` |
| `replace_range_live(address, text)` | Targeted replacement of a known span | `_resolve_address` then `setString(text)` |
| `delete_range_live(address)` | Deletion, which has no equivalent today | `setString("")` |
| `insert_paragraph_live(after_paragraph, text, style)` | Structural insert instead of appending to a run of text | `insertControlCharacter(PARAGRAPH_BREAK)` + `insertString` |
| `replace_text_live(search, replace, all, regex)` | Bulk edit without reading the document | `createReplaceDescriptor` + `replaceAll`; returns the replacement count |
| `set_track_changes_live(enabled)` | Lets the assistant and the human turn recording on or off deliberately, independent of the per-edit default below | `doc.RecordChanges` |

### Phase 3 — formatting and structure

| Tool | Purpose | Mechanism |
|---|---|---|
| `format_range_live(address, bold, italic, underline, font_size, font_name)` | Character formatting an assistant can actually target | the property writes `format_text` already does, on a resolved range |
| `apply_paragraph_style_live(address, style)` | Headings and lists; no paragraph styling exists today | `ParaStyleName` |
| `list_styles_live(family)` | Which style names this document actually has, so the assistant stops guessing | `doc.StyleFamilies` |
| `set_selection_live(address)` | Lets the assistant select, so existing selection-based tools become usable | `controller.select(range)` |

`format_text_live` stays for compatibility but gets its empty-selection bug
fixed: an empty or unreadable selection must return `success: false`.

### Phase 4 — reviewable editing

| Tool | Purpose | Mechanism |
|---|---|---|
| `add_comment_live(address, text)` | Suggest without touching the text | `com.sun.star.text.textfield.Annotation` via `insertTextContent` |
| `create_bookmark_live(address, name)` / addresses by bookmark | Stable handles across multi-step edits | `com.sun.star.text.Bookmark` |

For someone else's document, track changes plus comments is the only defensible
default, and it is worth reaching before the tool count grows further.

## Cross-cutting contract

Every mutating tool must:

1. **Group its undo.** Wrap the change in
   `doc.UndoManager.enterUndoContext("MCP: <tool>")` … `leaveUndoContext()`,
   with the leave in a `finally`. One assistant action must be one Ctrl+Z.
   Without this, a five-part edit becomes five undo steps and the human loses
   the ability to reject it — which costs more trust than any missing feature.
2. **Refuse read-only documents** via `doc.isReadonly()`, before touching
   anything.
3. **Record itself as a tracked change by default.** Every mutating tool takes
   `track_changes: bool = true`. When it is true the tool turns
   `doc.RecordChanges` on for the duration of its edit and restores the
   previous value afterwards, so the edit lands as a suggestion the human
   accepts or rejects, while the document's own setting is left as the human
   had it. Changes already recorded stay recorded — restoring the flag does not
   undo them. Passing `track_changes: false` writes directly, which is the
   right choice for a document the assistant itself created.

   Ordering matters and is part of the contract: enter the undo context, set
   the flag, edit, restore the flag, leave the undo context — the restore in a
   `finally`, so a failed edit cannot leave recording switched on.
4. **Resolve its address through `_resolve_address`**, never inline.
5. **Report the new position**: the affected paragraph index and the document's
   paragraph count after the edit.

Every tool, mutating or not, must:

6. **Cap returned text** at `MAX_TEXT_CHARS` per field, with `truncated` and
   the true `length`, as `get_cursor_info_live` does.
7. **Return `{"success": false, "error": …}` rather than raising**, per the
   existing plugin convention — exceptions inside UNO callbacks vanish.
8. **Accept an optional `document` argument** (a URL from
   `list_open_documents`), defaulting to the active document. With two windows
   open, "the active document" is otherwise a coin flip, and an assistant that
   edits the wrong file is worse than one that refuses.

## Verification

Two layers, because one is not enough — this is the direct lesson of the
`isinstance` defect, where a fully green fake-based suite coexisted with four
tools that had never worked.

**Unit tests** extend `tests/fake_writer.py`. The fakes must keep their two
faithfulness rules: documents answer `supportsService` and never satisfy
`isinstance`; ranges reject foreign texts. New fake surface needed: search,
paragraph styles, bookmarks, an undo manager that records enter/leave pairs.

**Live tests** are promoted from throwaway scripts into a committed harness,
`tests/live/writer_tools_check.py`, run explicitly under `/usr/bin/python3`
(which has the `python3-uno` bindings) against its own headless instance with a
separate user profile, so a developer's session is never touched. It is not
part of the default `pytest` run.

**No UNO mechanism in this document counts as working until the live harness
exercises it.** `findAll`, `replaceAll`, `ParaStyleName`, `UndoManager`,
`bAbsorb`, `RecordChanges`, `StyleFamilies` and `isReadonly` are all currently
API knowledge, not observed facts.

## Explicitly rejected

- **A generic `execute_uno_script` tool.** It would satisfy every requirement
  here at once, and that is precisely the problem: unreviewable, untestable,
  and an arbitrary-code path into the user's documents.
- **Run-level formatting fidelity** in `read_paragraphs_live` (every character
  span with its properties). Expensive per paragraph and not needed to edit
  text; paragraph style plus the caller's own targeting is enough.
- **Making paragraph indices stable.** Bookmarks solve the real cases; a
  shadow index would be a cache to invalidate.

## Success criteria

An assistant can, on a document it has not read in full: locate a passage by
search, understand the document's structure, rewrite the human's selection,
replace and delete a span it addressed itself, insert a paragraph with a style
— and every one of those changes is one undo step, refused outright on a
read-only document.

## Decisions taken

1. **Mutations are tracked by default.** Editing on the user's behalf is
   recorded as a suggestion unless the caller opts out, per the contract above.
   This pulls `set_track_changes_live` forward from phase 4 into phase 2, since
   phase 2 now depends on the mechanism.
2. **`find_text_live` keeps `context`.** The duplication with
   `read_paragraphs_live` is worth fewer round trips: a search hit is usually
   acted on immediately, and without context the assistant cannot tell which
   hit it wants.
3. **`set_selection_live` stays.** Moving the human's cursor is accepted as a
   side effect; it is what makes selection-based tools reachable at all.

## Remaining risks

- Whether toggling `doc.RecordChanges` inside an open undo context is itself
  undoable, or interacts badly with `enterUndoContext`, is unverified. If
  toggling turns out to be recorded as an undo step, the flag must be set
  outside the context instead. The live harness settles this before any phase 2
  tool is believed.
- Whether a tracked deletion leaves the original text in place (as Writer shows
  it struck through) changes what `delete_range_live` should report back. The
  harness must check what the document looks like after a tracked delete, not
  just that the call succeeded.
