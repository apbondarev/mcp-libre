---
name: libreoffice-mcp
description: Use when editing a document that is open in LibreOffice through the libreoffice MCP server - addressing text by paragraph, block, table cell, selection or anchor; keeping runs, links, comments and pictures through a rewrite; comments, tables, spelling and language; batching a plan into one undo step. Not for driving LibreOffice from the command line.
---

# Editing an open LibreOffice document through the MCP server

This server edits the documents open in LibreOffice, live and in place. There
are no files to read or write: a tool changes the document the reader is
looking at, and one tool call is one Ctrl+Z.

## Addressing

A place in a document is named in one of five ways:

```
{"paragraph": 7}                           a whole body paragraph
{"paragraph": 7, "offset": 4, "length": 9} part of one
{"paragraph": 7, "through": 19}            a block of whole paragraphs
{"table": "Table1", "cell": "A2"}          a cell; offsets count its own text
{"selection": true}                        what the reader has selected
{"anchor": "a7f3c1"}                       a place held from an earlier call
```

Paragraph numbers count body paragraphs and skip tables — and they move with
every insertion or deletion above them. An anchor does not: it points at the
text itself. Make anchors with `anchor`, or ask `find_text` and
`read_paragraphs` for them with `anchors: true`, and a plan survives its own
edits. `select` puts an address under the reader's eyes.

## Rewriting text without destroying it

Writing a string over a range flattens it: bold, italics, inline code (a
character style, not a font), hyperlinks, comments and inline pictures go in
one stroke. So `replace_range` and `replace_selection` **refuse** a range
holding more than one run, a link, a comment, an inline picture or a table,
unless `flatten: true` says the loss is wanted — and the refusal names what
it would cost. The route that keeps the look is `read_runs`, edit or
translate each run's text, then `replace_runs`, which leaves untouched every
run whose text did not change, and with it that run's comments and pictures.

## What is not text

A comment and a picture occupy no characters at all, so neither ever shows in
a string. `list_comments` and `list_images` report them with addresses,
`read_runs` says which run carries which, and `export_image` writes a picture
out. A caret in a table cell belongs to no body paragraph — `get_cursor_info`
says so, naming the table and the cell.

## Language

Writer spell-checks each run against its own locale, so correct Russian
written over English is underlined word by word. Pass `language` on the
replacement, or fix what is already written with `set_language`. A comment's
language comes from the "Comment" paragraph style at the moment the comment
is made, so `add_comment` takes `language` and changing an existing comment's
language means making it again.

## Tables

`list_tables` says what is there and where, `read_table` reads it,
`describe_table` reports borders, padding, backgrounds and column shares in
the units `format_table` takes, `create_table` puts one in — before the
paragraph its address names, or, with `replace: true`, in place of a block —
`format_table` dresses it, `delete_table` removes it.

## One edit, one undo

Every tool is already a single undo step. `batch_live` makes a whole plan
one: a list of `{tool, parameters}`, all checked before any of them runs,
with `on_error: "undo"` to take the batch back if a step fails. A step cannot
read an earlier step's result, so batch a plan already worked out.
`track_changes` has three states: omitted follows the document's own setting,
`true` records this edit anyway, `false` refuses to record it.

## Seeing the result

`render_page` gives the page as it prints — through LibreOffice alone, no
external program — which is how to check a table or a layout.
`check_spelling` reports what is misspelled and which language judged it.

## Spending fewer calls

`find_text` brings the paragraphs around each hit (`paragraphs_before`,
`paragraphs_after`) and hands out anchors, so one call does what would
otherwise be one per hit. `format_ranges` colours many pieces at once, and
`batch_live` writes many places at once.

## When a tool refuses

It refuses because it can see what the write would destroy, and the message
names the way round: read the runs, pass `flatten: true` knowingly, or
address the place by anchor. Read the refusal rather than retrying the call.

Every refusal carries a `code` beside the English, so a caller can branch on
it: `NO_DOCUMENT`, `WRONG_DOCUMENT_TYPE`, `READ_ONLY`, `INVALID_ADDRESS`,
`NOT_FOUND`, `INVALID_PARAMETER`, `WOULD_LOSE_FORMATTING`, `UNSUPPORTED`, or
`FAILED` when nothing better is known. Every result, refusal or not, also
carries `elapsed_ms` — what the call cost on the server.

## Checking your work

- `read_runs` the stretch you rewrote and confirm the styles, links and
  languages are the ones you meant to keep.
- `describe_table` with `runs: true` beside the table you copied the look
  from, when the work was a table.
- `render_page` on the page it landed on, to look at it as the reader will.
- Leave the document unsaved unless asked: that keeps Ctrl+Z available, and
  every call above is one undo step.

## Where the detail is

Every claim here was measured against a real LibreOffice and is recorded,
with the measurement, in the server repository's CLAUDE.md. When a tool
behaves unexpectedly, read the refusal it gives first: it names what it
would have destroyed and the route that keeps it.
