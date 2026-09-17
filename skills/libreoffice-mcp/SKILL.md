---
name: libreoffice-mcp
description: Use when editing a document that is open in LibreOffice through the libreoffice MCP server - addressing text by paragraph, block, table cell, selection or anchor; keeping runs, links, comments and pictures through a rewrite; comments, tables, spelling and language; batching a plan into one undo step. Not for driving LibreOffice from the command line.
---

# Editing an open LibreOffice document through the MCP server

This server edits the documents open in LibreOffice, live and in place. There
are no files to read or write: a tool changes the document the reader is
looking at, and one tool call is one Ctrl+Z.

## Which document

Every tool that acts on a document takes `document`, the URL
`list_open_documents` reports; leaving it out means the one the reader is
looking at. Name it whenever the work is about a particular document —
"the active document" is whichever window has focus, and that can change
under you. Only `create_document` and `list_open_documents` take none,
having no document to be pointed at.

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

A caption numbers itself: `insert_caption` writes "Figure 3: …" beside a
picture, a table or a paragraph, and a caption put in front of another
renumbers what follows. `insert_cross_reference` points at a heading, a
caption, a bookmark or a reference mark, and the field follows its target, so
"see Figure 3" stays right afterwards. Ask `list_reference_targets` what can
be pointed at — every target carries the `reference` object to hand straight
back — and `list_references` which references a document has and which of
them are broken, since a broken one shows only an error sentence in the text.

A table of contents writes itself: `insert_index` puts one in and
`update_indexes` writes them again from what the document says now — a
heading that has been translated shows in the old words until then. **An
index's entries are body paragraphs**, so putting one in or updating it moves
every paragraph number below it; the result says by how many, and a plan that
edits by address should write the indexes last.

An anchor lives as long as this session. A **bookmark** is the same handle kept
by the document itself: it is saved in the file, comes back when the document is
reopened, shows in the Navigator, and survives a rewrite of the very text it
covers. `add_bookmark`, `list_bookmarks`, `rename_bookmark` and `delete_bookmark`
work in names, and the address a bookmark reports is what the other tools take.

## Rewriting text without destroying it

Writing a string over a range flattens it: bold, italics, inline code (a
character style, not a font), hyperlinks, comments and inline pictures go in
one stroke. So `replace_range` and `replace_selection` **refuse** a range
holding more than one run, a link, a comment, an inline picture or a table,
unless `flatten: true` says the loss is wanted — and the refusal names what
it would cost. The route that keeps the look is `read_runs`, edit or
translate each run's text, then `replace_runs`, which leaves untouched every
run whose text did not change, and with it that run's comments and pictures.

## Fields

A date, a page number, the document's title: `list_fields` reports them with
what each shows and where it sits, `insert_field` puts one in,
`update_fields` makes them redraw (Writer's F9), `delete_field` takes one
away by its address. A field is the mirror of a comment — it **carries the
text it shows**, so its run looks like ordinary text and a rewrite destroys
the field and leaves the text. `read_runs` says which run is a field, and
`replace_range` refuses a range holding one unless `flatten` accepts it.

## What is not text

A comment and a picture occupy no characters at all, so neither ever shows in
a string. `list_comments` and `list_images` report them with addresses,
`read_runs` says which run carries which, and `export_image` writes a picture
out. A caret in a table cell belongs to no body paragraph — `get_cursor_info`
says so, naming the table and the cell.

## Review conversations

A reply is a comment joined to its parent, sitting on the parent's own
anchor: `add_comment` with `reply_to` and no address makes one, and
`list_comments` reports `reply_to` and `replies` on each, with `threads`,
`replies`, `unresolved` and the `authors` counted. It also narrows by
`author` and by `resolved`, which is how to ask what is still open.

`resolve_comments` marks them resolved or reopens them, and
`delete_comment` removes them; both pick in exactly one way — `comment_id`,
`author`, `address` or `all: true` — as the review tools do. Resolved
belongs to each note on its own, so the replies of whatever is picked follow
it, or Writer shows half a settled thread. Deleting anything whose replies
would be left behind is refused, and `with_replies: true` takes the thread.

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
`format_table` dresses it, `delete_table` removes it. Its shape changes with
`insert_table_rows` / `insert_table_columns` and the two that take them
away again — which report the text that went with them — `merge_table_cells` and
`split_table_cells`. `sort_table` puts the rows in the order of one column,
leaving the heading rows where they are; it moves the text between cells, so
a cell's background stays put while character formatting inside a moved cell
is flattened, and a cell of several runs is refused unless `flatten` says
otherwise.

## One edit, one undo

Every tool is already a single undo step. `batch_live` makes a whole plan
one: a list of `{tool, parameters}`, all checked before any of them runs,
with `on_error: "undo"` to take the batch back if a step fails. A step cannot
read an earlier step's result, so batch a plan already worked out.
`track_changes` has three states: omitted follows the document's own setting,
`true` records this edit anyway, `false` refuses to record it.

## Recorded changes, and how to work in a document that keeps them

**Look before editing.** `get_document_info` says in two numbers whether the
document is recording and how many changes are waiting; `list_tracked_changes`
says whose they are and what each one did — insert, delete or format, with the
text it covers and its address, scoped like the comments and narrowed by
`author`. A document in the middle of someone's review is different work from
a clean one.

**Then decide whether your own edit is recorded.** `track_changes` has three
states, and the default — leaving it out — follows the document, which is the
owner's decision and usually the right one. `true` records this edit anyway:
that is how to *propose* rather than change, which is what a translation of
someone else's document usually is. `false` refuses to record it even in a
recording document, for mechanical work nobody needs to approve; it overrides
the owner's setting for that one call and the setting is put back. The result
says what happened in `tracked` — **tell the reader**, because with recording
on the original stays in place struck through, and people read that as the
edit having failed.

**Do not rewrite text a change already marks.** It is refused, and rightly: a
deletion still waiting to be accepted would come back as ordinary text.
Settle that stretch first, then edit.

**Settling is the reader's decision, not yours.** Ask before accepting or
rejecting, and especially before `all: true`. `accept_tracked_changes` takes
the changes into the text, `reject_tracked_changes` puts the text back as it
was; each picks in exactly one way — `change_id`, `author`, `address` or
`all` — and refuses to guess. One call is one undo step, and the reader's own
selection is put back afterwards. Several edits and the settling of them can
go in one `batch_live`, which makes the whole piece of work one Ctrl+Z.

**Show what a reviewer would see**: `render_page` draws the markup — the
insertion underlined, the deletion struck through, the change bar in the
margin.

One thing to say plainly when it matters: a change is recorded under the
**office's user name**, not yours, so nothing in the document distinguishes
your edits from the reader's own.

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
