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
text itself. **Address by anchor wherever you can.** `find_text`,
`read_paragraphs` and `read_runs` hand out addresses that already carry one,
so the habit costs nothing: pass the address back **as it came**, whole, and
the anchor decides where the edit lands while the numbers beside it are only
what was true when you read. Three measured reasons:

  * your own edits move the ground. Insert a caption, a table of contents or a
    paragraph, and every number below it is wrong — a plan made from one
    search went stale as it was carried out;
  * the reader moves it too, and even between the steps of a single call. A
    batch of ten edits by number, while a reader pressed Enter above, hit
    **none** of its ten paragraphs and overwrote others; the same batch by
    anchor hit **ten of ten**. Reading just before the call does not help;
  * a number that has gone stale still names *some* paragraph, so a wrong
    write succeeds silently, where an anchor whose text was deleted refuses
    by name and tells you what it held.

`read_paragraphs` also **takes** an address as its `start` — the one a
previous read or a search handed back — so a long document is paged through
without a number ever being carried from one call to the next, and a block
address (`through`) says how many paragraphs to read. `count` is a window,
not a limit: 50 unasked, and a whole document can be had in one call — 6981
paragraphs came back in five seconds — though with anchors on it is refused
past 2000, which is how many this session keeps. `anchor` holds a place
you worked out some other way, `list_anchors` says where they point now (200
a call, `held` counting them all), `drop_anchors` lets go of the ones whose
work is done, and an anchor lives as long as this session.
`select` puts an address under the reader's eyes.

A caption numbers itself: `insert_caption` writes "Figure 3: …" beside a
picture, a table or a paragraph, and a caption put in front of another
renumbers what follows. `insert_cross_reference` points at a heading, a
caption, a bookmark or a reference mark, and the field follows its target, so
"see Figure 3" stays right afterwards. Ask `list_reference_targets` what can
be pointed at — every target carries the `reference` object to hand straight
back — and `list_references` which references a document has and which of
them are broken, since a broken one shows only an error sentence in the text.

Made a mess? `undo` takes the last edit back and `redo` puts it again. The
history is the document's, so the reader's own typing is in it too:
`list_undo_steps` says whose each step is, and `undo` stops at the first one
this server did not make unless `include_others` says otherwise.
`convert_text_to_table` turns separated lines into a table and
`convert_table_to_text` turns one back.

`find_by_style` finds every paragraph in a style — the code blocks of a
document, say — in milliseconds. What is applied *over* the styles is
`get_direct_formatting`, and `clear_direct_formatting` takes it off without
touching links or character styles. Styles themselves are written with
`create_style` (`from_style` clones one), `update_style`, `rename_style`,
`delete_style` and `replace_style`, which is what "use our house styles"
means; a built-in style can be changed but not renamed or removed.

`read_runs` over `{"paragraph": N, "through": M}` reads **every** paragraph
in the block, each run saying which paragraph it is in — one call instead of
one per paragraph. `replace_runs` writes one paragraph at a time and refuses
a block, since writing over it would collapse the paragraphs into one. A
colour comes **off** with `"automatic"`, which is what untouched text has;
`#000000` looks the same and is not the same thing.

To **move** a paragraph, use `move_paragraph` — never read-delete-rewrite,
which loses its comments and its pictures. `copy_paragraphs` copies a block
with everything on it, and `split_paragraph` / `merge_paragraphs` cut and
join.

`format_range` makes a hyperlink on text that is already there (`link`), and
`list_hyperlinks` / `remove_hyperlink` are the document-wide view: which
links there are, which of the ones pointing *inside* the document are broken,
and taking one away without touching its words.

The page's size, orientation, margins and columns belong to a page style:
`get_page_layout` and `set_page_layout` read and write them in millimetres,
and `set_page_break` starts a new page at a paragraph — switching page style
there is how a document turns landscape half way through. Writer rounds these
lengths, so A4 answers 210.01 mm wide: never compare one for equality.

The running title and the page number live in a **header or footer of a page
style**, not in the text: translating a document leaves them in the old
language until `set_header` / `set_footer` are used, where `{page}`,
`{pages}`, `{title}` and `{date}` become the fields a reader expects.
`remove_header_footer` throws the text away — Writer keeps nothing.

A **protected section is not protected from you**: Writer stops the reader's
keyboard and lets the API write straight through, so `replace_range`,
`replace_selection` and `replace_runs` refuse one themselves with READ_ONLY
and the section's name. `list_sections` says which parts of a document are
protected, hidden or set in columns; unprotect with `update_section`, or pass
`allow_protected: true` when you mean it.

A **footnote's mark is a character of the text**, not an invisible marker:
rewriting the run it sits in destroys the note. `read_runs` says which run is
a mark, `replace_runs` refuses to rewrite it and keeps it while the runs
around it change, and `list_notes`, `add_note`, `update_note` and
`delete_note` work on the notes themselves.

A table of contents writes itself: `insert_index` puts one in and
`update_indexes` writes them again from what the document says now — a
heading that has been translated shows in the old words until then. **An
index's entries are body paragraphs**, so putting one in or updating it moves
every paragraph number below it; the result says by how many, and a plan that
edits by address should write the indexes last.

A **bookmark** is an anchor the document itself keeps: it is saved in the
file, comes back when the document is reopened, shows in the Navigator, and
survives a rewrite of the very text it covers. `add_bookmark`,
`list_bookmarks`, `rename_bookmark` and `delete_bookmark` work in names, and
the address a bookmark reports is what the other tools take.

A **formula is not text**. It is an object sitting in the paragraph, so a
paragraph's `text` passes over it: a problem that says "the volume is 1/5 of
the whole" has `text` "the volume is  of the whole" — two spaces, and nothing
in the string to say a formula was there. A paragraph that holds one therefore
carries `formulas` and `text_with_formulas`, its text with each formula put
back in StarMath where it stands (`⟦formula: { frac { 1 } { 5 } }⟧`); read
that, not `text`, when reasoning about maths. `text` itself is unchanged,
because every offset in every address counts in it. `read_runs` lists them in
`formulas` too, and `list_formulas` gives them all with the words on either side,
`text_before` and `text_after`. `add_formula`, `set_formula` and `delete_formula` work by the
formula's name; `set_formula` is **not on the Undo list**, so it hands back the
text it replaced.

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
read an earlier step's result, so batch a plan already worked out. The
paragraph numbers a batch's steps name are **pinned before the first step
runs** and followed through it, so a step that adds a paragraph does not send
the steps after it to the wrong place, and a step whose paragraph has been
merged away is refused rather than written somewhere else. Addresses that
already carry an anchor need none of that.
`track_changes` has three states: omitted follows the document's own setting,
`true` records this edit anyway, `false` refuses to record it.

## When the reader is editing too

The reader can type while you work, and every call you make interleaves
with their keystrokes — measured on a real document:

- **Address by `anchor`** — the reason is above, and this is where it bites
  hardest: a reader pressing Enter moves the ground between the steps of one
  call, and reading the document just before does not help.
- **Do not move, copy or settle changes while the reader is typing.**
  `move_paragraph`, `copy_paragraphs`, `accept_tracked_changes`,
  `reject_tracked_changes` and `convert_table_to_text` work through the
  reader's own cursor, and a keystroke arriving mid-call replaced a line of
  the document with the letters typed.
- **Undoing a batch can erase the reader's work.** What they typed while the
  batch ran is folded into its undo step, so `undo` takes it too, and
  nothing in the step's title says so. Ask before undoing if they may have
  typed meanwhile.

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

`get_outline` is the map of a long document: every heading with its level and
its address, without reading a word of the body. A window nobody asked about
holds 200 headings, and that is a default rather than a limit — `count` may
ask for the lot, and 938 headings of a 519-page guide come back in one call
of a few seconds. `more` says a window ended early, and the last heading's
address is what to pass back as `start`.

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
"""
