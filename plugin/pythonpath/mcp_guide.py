"""The manual the server hands its client, so nobody has to learn it twice.

Everything here was measured against a real Writer and is recorded at length
in the repository's CLAUDE.md; this is the short form an assistant needs
before its first call. It is sent as the `instructions` of the MCP
initialize result, and scripts/write_skill.py turns the same text into the
skill shipped beside the server — one source, so the two cannot drift.

Light Markdown on purpose: it reads as plain text in a client that shows
instructions raw, and as a document in one that renders them.
"""

INSTRUCTIONS = """\
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
"""
