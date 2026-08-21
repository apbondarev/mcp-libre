# Writer Reading Tools (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the assistant the ability to see a Writer document's structure, read it in windows, and search it — with every result carrying an address that phase 2 can edit.

**Architecture:** Three new tools on the existing plugin bridge (`get_outline_live`, `read_paragraphs_live`, `find_text_live`) plus the two halves of the address contract: `_locate_range`, which turns a UNO range into an address, and `_resolve_address`, which turns an address back into a range. Both live in `uno_bridge.py`; every later phase depends on them, so this plan proves they round-trip before any mutation is built on top.

**Tech Stack:** Python 3 (LibreOffice's bundled interpreter, stdlib only), UNO/pyuno, pytest with faked UNO modules, plus a live harness run under `/usr/bin/python3` (`python3-uno`).

**Spec:** `docs/superpowers/specs/2026-08-18-writer-text-tools-design.md`

## Global Constraints

- Plugin code under `plugin/pythonpath/` runs in LibreOffice's bundled Python: **stdlib only**. No `pip` dependencies, no `mcp`, no `httpx`, no `pydantic`.
- Tools return `{"success": false, "error": …}` and **never raise** — exceptions inside UNO callbacks vanish.
- Every returned text field is capped at `MAX_TEXT_CHARS` (2000) via `_text_payload`, which reports `truncated` plus the true `length`.
- `isinstance` is useless on UNO proxies (every one is `<class 'pyuno'>`). Type checks go through `_supports(obj, service)` with `WRITER_SERVICE = "com.sun.star.text.TextDocument"`.
- A text range belongs to the text that owns it. Use `range.getText()`, never assume `doc.getText()`. Passing a foreign range to the body text throws *"End of content node doesn't have the proper start node"*.
- Paragraph indices are 0-based, count only paragraphs in the body enumeration, and skip tables.
- Adding a tool is **three edits**: a `UNOBridge` method, a `*_live` handler on `LibreOfficeMCPServer`, and a `self.tools[...]` entry in `_register_tools`.
- No UNO mechanism counts as working until the live harness exercises it. `OutlineLevel`, `createSearchDescriptor`, `findAll` and `ParaStyleName` are currently API knowledge, not observed facts.
- Fakes in `tests/fake_writer.py` keep their two faithfulness rules: documents answer `supportsService` and **never** satisfy `isinstance`; a text rejects ranges owned by another text.
- **Commit messages carry no `Co-Authored-By` trailer and no AI attribution.**
- Test commands: `uv run pytest tests/<file> -v`. The live harness is not part of the pytest run.

---

## File Structure

| File | Responsibility |
|---|---|
| `plugin/pythonpath/uno_bridge.py` (modify) | The address contract (`_locate_range`, `_resolve_address`, `_paragraph_at`, `AddressError`) and the three reading methods |
| `plugin/pythonpath/mcp_server.py` (modify) | Three tool registrations and their `*_live` handlers |
| `tests/fake_writer.py` (modify) | Fake surface for cursor movement, paragraph properties, and search |
| `tests/test_address.py` (create) | The address contract, including the round-trip property |
| `tests/test_reading_tools.py` (create) | `get_outline`, `read_paragraphs`, `find_text` |
| `tests/test_cursor_info.py` (modify) | One test for the extracted `_locate_range` |
| `tests/test_document_targeting.py` (create) | Naming which open document a tool acts on |
| `tests/live/writer_tools_check.py` (create) | Live verification against a real headless LibreOffice |
| `CLAUDE.md`, `docs/LIBREOFFICE_MCP_EXTENSION.md` (modify) | Tool count and tool list |

---

## Task 1: Extract the range→address half of the contract

`get_cursor_info` already computes an address; `find_text` needs the same computation per hit. Extract it first, under the protection of the 19 tests that already cover `get_cursor_info`.

**Files:**
- Modify: `plugin/pythonpath/uno_bridge.py` (add `_locate_range`, rewrite the middle of `get_cursor_info`)
- Test: `tests/test_cursor_info.py`

**Interfaces:**
- Consumes: `_locate_paragraph(text, paragraph_start) -> (index|None, chars_before|None)`, `_text_payload(str) -> dict` — both already exist.
- Produces: `_locate_range(doc, text_range) -> (address, paragraph_cursor, chars_before)` where `address` is `{"paragraph": int|None, "offset": int, "length": int}`. Task 2 and Task 5 both consume it.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cursor_info.py`, and extend its import from `tests.fake_writer` to also bring in `FakeRange`:

```python
def test_locate_range_reports_the_address_of_a_range(bridge):
    doc = writer_doc(PARAGRAPHS, caret=(0, 0))
    target = FakeRange(doc.getText(), (1, 7), (1, 13))

    address, paragraph_cursor, chars_before = bridge._locate_range(doc, target)

    assert address == {"paragraph": 1, "offset": 7, "length": 6}
    assert paragraph_cursor.getString() == "Second paragraph here."
    assert chars_before == 17  # "First paragraph." + the paragraph break
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_cursor_info.py::test_locate_range_reports_the_address_of_a_range -v`
Expected: FAIL with `AttributeError: 'UNOBridge' object has no attribute '_locate_range'`

- [ ] **Step 3: Add the method**

Insert into `UNOBridge` immediately before `_locate_paragraph`:

```python
    def _locate_range(self, doc: Any, text_range: Any) -> tuple:
        """
        Locate a range within the document

        Returns (address, paragraph_cursor, chars_before_paragraph), where
        address is {"paragraph": index or None, "offset": int, "length": int},
        paragraph_cursor spans the paragraph holding the range start, and
        chars_before_paragraph is None whenever the index is None.

        The cursors come from the text owning the range, which inside a table
        cell or a frame is not the body text.
        """
        owner = text_range.getText()
        start = text_range.getStart()

        offset_cursor = owner.createTextCursorByRange(start)
        offset_cursor.gotoStartOfParagraph(True)
        offset = len(offset_cursor.getString())

        paragraph_cursor = owner.createTextCursorByRange(start)
        paragraph_cursor.gotoStartOfParagraph(False)
        paragraph_cursor.gotoEndOfParagraph(True)

        index, chars_before = self._locate_paragraph(
            doc.getText(), paragraph_cursor.getStart())

        address = {
            "paragraph": index,
            "offset": offset,
            "length": len(text_range.getString())
        }
        return address, paragraph_cursor, chars_before
```

- [ ] **Step 4: Rewrite `get_cursor_info` to use it**

Replace everything in `get_cursor_info` from `caret = view_cursor.getStart()` down to (and including) the `index, chars_before = self._locate_paragraph(...)` call with:

```python
            caret = view_cursor.getStart()
            address, paragraph_cursor, chars_before = self._locate_range(doc, caret)
            index = address["paragraph"]
            offset_in_paragraph = address["offset"]
```

Leave the `info = {...}` dict that follows untouched.

- [ ] **Step 5: Run the whole cursor suite**

Run: `uv run pytest tests/test_cursor_info.py tests/test_document_type_dispatch.py -v`
Expected: PASS, 26 tests. A failure here means the refactor changed behaviour — fix the code, not the tests.

- [ ] **Step 6: Commit**

```bash
git add plugin/pythonpath/uno_bridge.py tests/test_cursor_info.py
git commit -m "Extract _locate_range from get_cursor_info

Turning a range into an address is needed per search hit as well as for the
caret, so it becomes one method with its own test."
```

---

## Task 2: The address→range half of the contract

**Files:**
- Modify: `plugin/pythonpath/uno_bridge.py`
- Modify: `tests/fake_writer.py` (cursor movement)
- Test: `tests/test_address.py` (create)

**Interfaces:**
- Consumes: `_locate_range` (Task 1), `_supports`, `WRITER_SERVICE`.
- Produces: `AddressError` (module-level exception), `_paragraph_at(text, index) -> paragraph|None`, `_resolve_address(doc, address) -> XTextRange`. Every phase 2 tool consumes `_resolve_address`.

**Decision to honour:** the resolver returns a collapsed range for a collapsed selection rather than rejecting it — inserting at a caret is legitimate. Callers that need non-empty content check for themselves. This is what keeps `format_text`'s silent-success bug from being re-created inside the resolver.

- [ ] **Step 1: Add cursor movement to the fake**

`goRight` is how the resolver walks to an offset. Add to `FakeTextCursor` in `tests/fake_writer.py`:

```python
    def goRight(self, count, expand):
        """Move right by count characters, a paragraph break counting as one."""
        paragraph, offset = self.pos
        remaining = count
        while remaining > 0:
            room = len(self.model.paragraphs[paragraph]) - offset
            if remaining <= room:
                offset += remaining
                remaining = 0
            elif paragraph + 1 < len(self.model.paragraphs):
                remaining -= room + 1
                paragraph += 1
                offset = 0
            else:
                offset = len(self.model.paragraphs[paragraph])
                break
        self.pos = (paragraph, offset)
        if not expand:
            self.mark = self.pos
        return remaining == 0
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_address.py`:

```python
"""Tests for the address contract: {"paragraph": i, "offset": k, "length": n}.

The round-trip test is the important one: a search hit's address must resolve
back to the text that was found, or phase 2 will edit the wrong span.
"""

import pytest

from tests.fake_writer import (
    FakeRange,
    FakeSelection,
    FakeTableCellSelection,
    writer_doc,
)
from tests.uno_stubs import install_uno_stubs

install_uno_stubs()

from uno_bridge import AddressError, UNOBridge  # noqa: E402

PARAGRAPHS = [
    "First paragraph.",  # 16 chars
    "Second paragraph here.",  # 22 chars
    "Third one.",  # 10 chars
]


@pytest.fixture
def bridge():
    return UNOBridge()


@pytest.fixture
def doc():
    return writer_doc(PARAGRAPHS, caret=(0, 0))


def test_resolves_a_whole_paragraph(bridge, doc):
    resolved = bridge._resolve_address(doc, {"paragraph": 1})

    assert resolved.getString() == "Second paragraph here."


def test_resolves_an_offset_and_length_inside_a_paragraph(bridge, doc):
    resolved = bridge._resolve_address(doc, {"paragraph": 1, "offset": 7, "length": 9})

    assert resolved.getString() == "paragraph"


def test_offset_without_length_runs_to_the_end_of_the_paragraph(bridge, doc):
    resolved = bridge._resolve_address(doc, {"paragraph": 1, "offset": 7})

    assert resolved.getString() == "paragraph here."


def test_resolves_the_current_selection(bridge):
    doc = writer_doc(PARAGRAPHS, caret=(2, 0), selection_spans=[((2, 0), (2, 5))])

    resolved = bridge._resolve_address(doc, {"selection": True})

    assert resolved.getString() == "Third"


def test_resolves_a_collapsed_selection_to_an_empty_range(bridge, doc):
    resolved = bridge._resolve_address(doc, {"selection": True})

    assert resolved.getString() == ""


def test_skips_tables_when_counting_paragraphs(bridge):
    doc = writer_doc(PARAGRAPHS, caret=(0, 0), enumeration_items=[0, 1, "table", 2])

    resolved = bridge._resolve_address(doc, {"paragraph": 2})

    assert resolved.getString() == "Third one."


def test_rejects_a_paragraph_past_the_end(bridge, doc):
    with pytest.raises(AddressError, match="no body paragraph 9"):
        bridge._resolve_address(doc, {"paragraph": 9})


def test_rejects_an_offset_past_the_end_of_the_paragraph(bridge, doc):
    with pytest.raises(AddressError, match="outside paragraph"):
        bridge._resolve_address(doc, {"paragraph": 2, "offset": 50})


def test_rejects_a_length_running_past_the_end_of_the_paragraph(bridge, doc):
    with pytest.raises(AddressError, match="past the end"):
        bridge._resolve_address(doc, {"paragraph": 2, "offset": 5, "length": 50})


def test_rejects_a_negative_paragraph(bridge, doc):
    with pytest.raises(AddressError, match="non-negative"):
        bridge._resolve_address(doc, {"paragraph": -1})


def test_rejects_an_address_with_neither_key(bridge, doc):
    with pytest.raises(AddressError, match="'paragraph' or 'selection'"):
        bridge._resolve_address(doc, {"offset": 3})


def test_rejects_a_non_object_address(bridge, doc):
    with pytest.raises(AddressError, match="must be an object"):
        bridge._resolve_address(doc, "paragraph 1")


def test_rejects_an_empty_selection_collection(bridge, doc):
    doc.getCurrentController()._selection = FakeSelection(doc.getText(), [])

    with pytest.raises(AddressError, match="nothing is selected"):
        bridge._resolve_address(doc, {"selection": True})


def test_rejects_a_selection_that_is_not_text(bridge, doc):
    doc.getCurrentController()._selection = FakeTableCellSelection()

    with pytest.raises(AddressError, match="not a text range"):
        bridge._resolve_address(doc, {"selection": True})


def test_address_round_trips_through_locate_and_resolve(bridge, doc):
    original = FakeRange(doc.getText(), (1, 7), (1, 16))
    address, _, _ = bridge._locate_range(doc, original)

    resolved = bridge._resolve_address(doc, address)

    assert resolved.getString() == original.getString() == "paragraph"
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_address.py -v`
Expected: collection ERROR — `ImportError: cannot import name 'AddressError' from 'uno_bridge'`

- [ ] **Step 4: Implement the resolver**

Add the exception next to `_supports` in `uno_bridge.py`:

```python
class AddressError(Exception):
    """An address that cannot be resolved to a text range"""
```

Add both methods to `UNOBridge`, immediately after `_locate_range`:

```python
    def _paragraph_at(self, text: Any, index: Any) -> Any:
        """
        The index-th body paragraph, or None when there is no such paragraph

        Tables are skipped, so indices match what _locate_paragraph reports.
        """
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise AddressError(
                f"paragraph must be a non-negative integer, got {index!r}")

        position = 0
        enumeration = text.createEnumeration()
        while enumeration.hasMoreElements():
            element = enumeration.nextElement()
            if not hasattr(element, "getStart"):
                continue
            if position == index:
                return element
            position += 1
        return None

    def _resolve_address(self, doc: Any, address: Any) -> Any:
        """
        Turn an address into a text range

        Accepts {"paragraph": i, "offset": k, "length": n} against the body
        text, where offset defaults to 0 and an omitted length means the rest
        of the paragraph, or {"selection": true} for the current selection.
        Raises AddressError for anything it cannot resolve.

        A collapsed selection resolves to an empty range rather than an error:
        inserting at a caret is legitimate, so callers needing actual content
        check for themselves.
        """
        if not isinstance(address, dict):
            raise AddressError(f"address must be an object, got {type(address).__name__}")

        if address.get("selection"):
            controller = doc.getCurrentController()
            if not controller:
                raise AddressError("document has no view, so it has no selection")
            try:
                selection = controller.getSelection()
                count = selection.getCount()
            except Exception as e:
                raise AddressError(f"the selection is not a text range: {e}")
            if count < 1:
                raise AddressError("nothing is selected")
            return selection.getByIndex(0)

        if "paragraph" not in address:
            raise AddressError("address needs either 'paragraph' or 'selection'")

        paragraph = self._paragraph_at(doc.getText(), address["paragraph"])
        if paragraph is None:
            raise AddressError(f"no body paragraph {address['paragraph']}")

        paragraph_length = len(paragraph.getString())
        offset = address.get("offset", 0)
        length = address.get("length")

        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0 \
                or offset > paragraph_length:
            raise AddressError(
                f"offset {offset!r} is outside paragraph {address['paragraph']}, "
                f"which holds {paragraph_length} characters")

        cursor = paragraph.getText().createTextCursorByRange(paragraph.getStart())
        if offset:
            cursor.goRight(offset, False)

        if length is None:
            cursor.gotoEndOfParagraph(True)
        else:
            if not isinstance(length, int) or isinstance(length, bool) or length < 0 \
                    or offset + length > paragraph_length:
                raise AddressError(
                    f"length {length!r} from offset {offset} runs past the end of "
                    f"paragraph {address['paragraph']}")
            cursor.goRight(length, True)
        return cursor
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_address.py -v`
Expected: PASS, 15 tests.

- [ ] **Step 6: Run everything, then commit**

Run: `uv run pytest tests/test_address.py tests/test_cursor_info.py tests/test_document_type_dispatch.py -v`
Expected: PASS, 41 tests.

```bash
git add plugin/pythonpath/uno_bridge.py tests/fake_writer.py tests/test_address.py
git commit -m "Add the address resolver for Writer text

One address type, resolved in one place: a body paragraph index with an
optional offset and length, or the current selection. The round-trip test
pins the contract that later editing tools depend on — an address produced
from a range must resolve back to the same text."
```

---

## Task 3: `read_paragraphs_live`

**Files:**
- Modify: `plugin/pythonpath/uno_bridge.py`, `plugin/pythonpath/mcp_server.py`
- Modify: `tests/fake_writer.py` (paragraph styles)
- Test: `tests/test_reading_tools.py` (create)

**Interfaces:**
- Consumes: `_text_payload`, `_supports`, `WRITER_SERVICE`.
- Produces: `_get_property(obj, name, default)` (module-level, used by Task 4), `UNOBridge.read_paragraphs(start, count, doc) -> dict`, tool `read_paragraphs_live`.

- [ ] **Step 1: Give fake paragraphs a style**

In `tests/fake_writer.py`, replace `FakeText.__init__` and `FakeParagraph` with:

```python
class FakeParagraph(FakeRange):
    def __init__(self, model, index):
        super().__init__(model, (index, 0), (index, len(model.paragraphs[index])))
        self.index = index
        self.ParaStyleName = model.styles[index]
        if model.expose_outline_level:
            self.OutlineLevel = model.outline_levels[index]
```

and in `FakeText`:

```python
    def __init__(self, paragraphs, enumeration_items=None, styles=None,
                 outline_levels=None, expose_outline_level=True):
        self.paragraphs = list(paragraphs)
        self.styles = list(styles) if styles else ["Standard"] * len(self.paragraphs)
        self.outline_levels = (list(outline_levels) if outline_levels
                               else [0] * len(self.paragraphs))
        self.expose_outline_level = expose_outline_level
        self.enumeration_items = (
            list(range(len(self.paragraphs)))
            if enumeration_items is None
            else list(enumeration_items)
        )
```

Keep the rest of `FakeText` (including `_own`) exactly as it is.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_reading_tools.py`:

```python
"""Tests for the phase 1 reading tools: read_paragraphs, get_outline, find_text."""

import asyncio

import pytest

from tests.fake_writer import FakeCalcDoc, writer_doc
from tests.uno_stubs import install_uno_stubs

install_uno_stubs()

from uno_bridge import MAX_PARAGRAPH_COUNT, MAX_TEXT_CHARS, UNOBridge  # noqa: E402

PARAGRAPHS = ["Alpha.", "Beta.", "Gamma.", "Delta.", "Epsilon."]


@pytest.fixture
def bridge():
    return UNOBridge()


def test_reads_a_window_of_paragraphs(bridge):
    doc = writer_doc(PARAGRAPHS, caret=(0, 0))

    result = bridge.read_paragraphs(start=1, count=2, doc=doc)

    assert result["success"] is True
    assert [p["paragraph"] for p in result["paragraphs"]] == [1, 2]
    assert [p["text"] for p in result["paragraphs"]] == ["Beta.", "Gamma."]


def test_reports_the_total_paragraph_count(bridge):
    doc = writer_doc(PARAGRAPHS, caret=(0, 0))

    result = bridge.read_paragraphs(start=0, count=2, doc=doc)

    assert result["total_paragraphs"] == 5


def test_includes_the_paragraph_style(bridge):
    doc = writer_doc(PARAGRAPHS, caret=(0, 0),
                     styles=["Heading 1", "Standard", "Standard", "Standard", "Quotations"])

    result = bridge.read_paragraphs(start=0, count=1, doc=doc)

    assert result["paragraphs"][0]["style"] == "Heading 1"


def test_caps_the_requested_count(bridge):
    doc = writer_doc(["p"] * (MAX_PARAGRAPH_COUNT + 50), caret=(0, 0))

    result = bridge.read_paragraphs(start=0, count=MAX_PARAGRAPH_COUNT + 50, doc=doc)

    assert len(result["paragraphs"]) == MAX_PARAGRAPH_COUNT
    assert result["total_paragraphs"] == MAX_PARAGRAPH_COUNT + 50


def test_returns_nothing_when_start_is_past_the_end(bridge):
    doc = writer_doc(PARAGRAPHS, caret=(0, 0))

    result = bridge.read_paragraphs(start=99, count=10, doc=doc)

    assert result["success"] is True
    assert result["paragraphs"] == []
    assert result["total_paragraphs"] == 5


def test_skips_tables_when_numbering_paragraphs(bridge):
    doc = writer_doc(PARAGRAPHS, caret=(0, 0),
                     enumeration_items=[0, "table", 1, 2, 3, 4])

    result = bridge.read_paragraphs(start=1, count=1, doc=doc)

    assert result["paragraphs"][0]["text"] == "Beta."
    assert result["total_paragraphs"] == 5


def test_truncates_a_long_paragraph_but_reports_its_true_length(bridge):
    doc = writer_doc(["z" * (MAX_TEXT_CHARS + 10)], caret=(0, 0))

    entry = bridge.read_paragraphs(start=0, count=1, doc=doc)["paragraphs"][0]

    assert len(entry["text"]) == MAX_TEXT_CHARS
    assert entry["length"] == MAX_TEXT_CHARS + 10
    assert entry["truncated"] is True


def test_rejects_a_negative_start(bridge):
    doc = writer_doc(PARAGRAPHS, caret=(0, 0))

    result = bridge.read_paragraphs(start=-1, count=1, doc=doc)

    assert result["success"] is False
    assert "start" in result["error"]


def test_read_paragraphs_rejects_a_non_writer_document(bridge):
    result = bridge.read_paragraphs(doc=FakeCalcDoc())

    assert result["success"] is False
    assert "writer" in result["error"].lower()


def test_read_paragraphs_tool_is_registered_and_dispatches():
    from mcp_server import LibreOfficeMCPServer

    server = LibreOfficeMCPServer()
    doc = writer_doc(PARAGRAPHS, caret=(0, 0))
    server.uno_bridge.get_active_document = lambda: doc

    assert "read_paragraphs_live" in server.tools

    result = asyncio.run(server.execute_tool("read_paragraphs_live",
                                             {"start": 0, "count": 2}))

    assert result["success"] is True
    assert len(result["paragraphs"]) == 2
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_reading_tools.py -v`
Expected: collection ERROR — `ImportError: cannot import name 'MAX_PARAGRAPH_COUNT'`

- [ ] **Step 4: Implement the bridge method**

Add next to `MAX_TEXT_CHARS` in `uno_bridge.py`:

```python
# Paragraph window sizes for read_paragraphs
DEFAULT_PARAGRAPH_COUNT = 50
MAX_PARAGRAPH_COUNT = 200


def _get_property(obj: Any, name: str, default: Any = None) -> Any:
    """Read a UNO property, falling back when the object does not carry it"""
    try:
        return getattr(obj, name)
    except Exception:
        return default
```

Add to `UNOBridge`, after `get_cursor_info`:

```python
    def read_paragraphs(self, start: int = 0,
                        count: int = DEFAULT_PARAGRAPH_COUNT,
                        doc: Any = None) -> Dict[str, Any]:
        """
        Read a window of body paragraphs with their indices and styles

        count is capped at MAX_PARAGRAPH_COUNT. total_paragraphs always
        reflects the whole document, so the caller can page through it.
        """
        try:
            if doc is None:
                doc = self.get_active_document()

            if not doc:
                return {"success": False, "error": "No document available"}

            if not _supports(doc, WRITER_SERVICE):
                return {
                    "success": False,
                    "error": f"Reading paragraphs is only available for Writer "
                             f"documents, got {self._get_document_type(doc)}"
                }

            if not isinstance(start, int) or isinstance(start, bool) or start < 0:
                return {"success": False,
                        "error": f"start must be a non-negative integer, got {start!r}"}

            window = max(1, min(int(count), MAX_PARAGRAPH_COUNT))
            paragraphs = []
            total = 0

            enumeration = doc.getText().createEnumeration()
            while enumeration.hasMoreElements():
                element = enumeration.nextElement()
                if not hasattr(element, "getStart"):
                    continue
                if start <= total < start + window:
                    entry = _text_payload(element.getString())
                    entry["paragraph"] = total
                    entry["style"] = _get_property(element, "ParaStyleName")
                    paragraphs.append(entry)
                total += 1

            return {
                "success": True,
                "paragraphs": paragraphs,
                "start": start,
                "count": len(paragraphs),
                "total_paragraphs": total
            }

        except Exception as e:
            logger.error(f"Failed to read paragraphs: {e}")
            return {"success": False, "error": str(e)}
```

- [ ] **Step 5: Register the tool**

In `mcp_server.py`, add to `_register_tools` before the `# Document saving tools` block:

```python
        # Reading tools
        self.tools["read_paragraphs_live"] = {
            "description": "Read a window of paragraphs from the active Writer document, with their indices and styles",
            "parameters": {
                "type": "object",
                "properties": {
                    "start": {
                        "type": "integer",
                        "description": "Index of the first paragraph to read (0-based)",
                        "default": 0
                    },
                    "count": {
                        "type": "integer",
                        "description": "How many paragraphs to read (max 200)",
                        "default": 50
                    }
                }
            },
            "handler": self.read_paragraphs_live
        }
        
```

and the handler, before `save_document_live`:

```python
    def read_paragraphs_live(self, start: int = 0, count: int = 50) -> Dict[str, Any]:
        """Read a window of paragraphs from the active Writer document"""
        return self.uno_bridge.read_paragraphs(start=start, count=count)
    
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_reading_tools.py -v`
Expected: PASS, 10 tests.

- [ ] **Step 7: Commit**

```bash
git add plugin/pythonpath/ tests/
git commit -m "Add read_paragraphs_live

Paging through a document with paragraph indices and styles, instead of
pulling the whole text into context. total_paragraphs is always the real
count so the caller can page."
```

---

## Task 4: `get_outline_live`

**Files:**
- Modify: `plugin/pythonpath/uno_bridge.py`, `plugin/pythonpath/mcp_server.py`
- Test: `tests/test_reading_tools.py` (append)

**Interfaces:**
- Consumes: `_get_property`, `_text_payload`, `_supports` (Task 3).
- Produces: `UNOBridge.get_outline(doc) -> dict`, `MAX_OUTLINE_ENTRIES`, tool `get_outline_live`.

- [ ] **Step 1: Probe the real API before writing code against it**

`OutlineLevel` is API knowledge, not an observed fact. Confirm it exists and what it holds:

```bash
cat > /tmp/probe_outline.py <<'PY'
import subprocess, sys, time
import uno
from com.sun.star.text.ControlCharacter import PARAGRAPH_BREAK
PORT, PROFILE = 2011, "/tmp/probe_outline_profile"
p = subprocess.Popen(["soffice", f"-env:UserInstallation=file://{PROFILE}",
                      "--headless", "--norestore", "--nologo", "--nodefault",
                      f"--accept=socket,host=127.0.0.1,port={PORT};urp;"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    local = uno.getComponentContext()
    r = local.ServiceManager.createInstanceWithContext("com.sun.star.bridge.UnoUrlResolver", local)
    for _ in range(60):
        try:
            ctx = r.resolve(f"uno:socket,host=127.0.0.1,port={PORT};urp;StarOffice.ComponentContext"); break
        except Exception: time.sleep(1)
    desktop = ctx.ServiceManager.createInstanceWithContext("com.sun.star.frame.Desktop", ctx)
    doc = desktop.loadComponentFromURL("private:factory/swriter", "_blank", 0, ())
    text = doc.getText(); cur = text.createTextCursor()
    for style, body in [("Heading 1", "Chapter"), ("Standard", "Body text"),
                        ("Heading 2", "Section")]:
        cur.ParaStyleName = style
        text.insertString(cur, body, False)
        text.insertControlCharacter(cur, PARAGRAPH_BREAK, False)
    e = text.createEnumeration()
    while e.hasMoreElements():
        para = e.nextElement()
        print(repr(para.getString()), "style=", para.ParaStyleName,
              "OutlineLevel=", getattr(para, "OutlineLevel", "ABSENT"))
    doc.setModified(False); doc.close(True); desktop.terminate()
finally:
    time.sleep(2)
    if p.poll() is None: p.terminate()
PY
/usr/bin/python3 /tmp/probe_outline.py
```

Expected: each paragraph prints a `style=` and an integer `OutlineLevel=`, with `Heading 1` → 1 and `Standard` → 0. **If `OutlineLevel` prints `ABSENT`, or headings report 0**, the implementation in Step 3 must rely on the `ParaStyleName` fallback alone — record what you saw in the commit message.

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_reading_tools.py`:

```python
OUTLINE_PARAGRAPHS = ["Chapter One", "Body text here.", "Section A", "More body."]
OUTLINE_STYLES = ["Heading 1", "Standard", "Heading 2", "Standard"]


def test_lists_headings_with_their_levels(bridge):
    doc = writer_doc(OUTLINE_PARAGRAPHS, caret=(0, 0), styles=OUTLINE_STYLES,
                     outline_levels=[1, 0, 2, 0])

    result = bridge.get_outline(doc)

    assert result["success"] is True
    assert result["headings"] == [
        {"paragraph": 0, "level": 1, "text": "Chapter One"},
        {"paragraph": 2, "level": 2, "text": "Section A"},
    ]


def test_reports_the_paragraph_count_alongside_the_outline(bridge):
    doc = writer_doc(OUTLINE_PARAGRAPHS, caret=(0, 0), styles=OUTLINE_STYLES,
                     outline_levels=[1, 0, 2, 0])

    result = bridge.get_outline(doc)

    assert result["total_paragraphs"] == 4


def test_falls_back_to_style_names_when_outline_level_is_absent(bridge):
    doc = writer_doc(OUTLINE_PARAGRAPHS, caret=(0, 0), styles=OUTLINE_STYLES,
                     expose_outline_level=False)

    result = bridge.get_outline(doc)

    assert [h["paragraph"] for h in result["headings"]] == [0, 2]
    assert [h["level"] for h in result["headings"]] == [1, 2]


def test_returns_an_empty_outline_for_a_document_without_headings(bridge):
    doc = writer_doc(["Just body.", "More body."], caret=(0, 0))

    result = bridge.get_outline(doc)

    assert result["success"] is True
    assert result["headings"] == []


def test_caps_the_outline_and_flags_it(bridge):
    from uno_bridge import MAX_OUTLINE_ENTRIES

    count = MAX_OUTLINE_ENTRIES + 10
    doc = writer_doc([f"Heading {i}" for i in range(count)], caret=(0, 0),
                     styles=["Heading 1"] * count,
                     outline_levels=[1] * count)

    result = bridge.get_outline(doc)

    assert len(result["headings"]) == MAX_OUTLINE_ENTRIES
    assert result["truncated"] is True


def test_get_outline_rejects_a_non_writer_document(bridge):
    result = bridge.get_outline(FakeCalcDoc())

    assert result["success"] is False
    assert "writer" in result["error"].lower()


def test_get_outline_tool_is_registered_and_dispatches():
    from mcp_server import LibreOfficeMCPServer

    server = LibreOfficeMCPServer()
    doc = writer_doc(OUTLINE_PARAGRAPHS, caret=(0, 0), styles=OUTLINE_STYLES,
                     outline_levels=[1, 0, 2, 0])
    server.uno_bridge.get_active_document = lambda: doc

    assert "get_outline_live" in server.tools

    result = asyncio.run(server.execute_tool("get_outline_live", {}))

    assert [h["text"] for h in result["headings"]] == ["Chapter One", "Section A"]
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_reading_tools.py -v -k outline`
Expected: FAIL with `AttributeError: 'UNOBridge' object has no attribute 'get_outline'`

- [ ] **Step 4: Implement**

Add next to `MAX_PARAGRAPH_COUNT`:

```python
# Cap on headings returned by get_outline
MAX_OUTLINE_ENTRIES = 200


def _heading_level(paragraph: Any) -> int:
    """
    Outline level of a paragraph, 0 when it is body text

    OutlineLevel covers custom styles that were given a level; the style-name
    check is the fallback for builds that do not expose the property.
    """
    level = _get_property(paragraph, "OutlineLevel", 0)
    if isinstance(level, int) and not isinstance(level, bool) and level > 0:
        return level

    style = _get_property(paragraph, "ParaStyleName", "") or ""
    if style.startswith("Heading "):
        suffix = style[len("Heading "):].strip()
        if suffix.isdigit():
            return int(suffix)
    return 0
```

Add to `UNOBridge`, after `read_paragraphs`:

```python
    def get_outline(self, doc: Any = None) -> Dict[str, Any]:
        """
        List the document's headings with the paragraph index of each

        Gives an assistant a map of a long document without reading it, and
        every entry doubles as an address to read or edit from.
        """
        try:
            if doc is None:
                doc = self.get_active_document()

            if not doc:
                return {"success": False, "error": "No document available"}

            if not _supports(doc, WRITER_SERVICE):
                return {
                    "success": False,
                    "error": f"An outline is only available for Writer documents, "
                             f"got {self._get_document_type(doc)}"
                }

            headings = []
            total = 0
            dropped = 0

            enumeration = doc.getText().createEnumeration()
            while enumeration.hasMoreElements():
                element = enumeration.nextElement()
                if not hasattr(element, "getStart"):
                    continue
                level = _heading_level(element)
                if level > 0:
                    if len(headings) < MAX_OUTLINE_ENTRIES:
                        headings.append({
                            "paragraph": total,
                            "level": level,
                            "text": element.getString()[:MAX_TEXT_CHARS]
                        })
                    else:
                        dropped += 1
                total += 1

            if dropped:
                logger.info(f"Outline truncated, {dropped} headings dropped")

            return {
                "success": True,
                "headings": headings,
                "total_paragraphs": total,
                "truncated": dropped > 0
            }

        except Exception as e:
            logger.error(f"Failed to get outline: {e}")
            return {"success": False, "error": str(e)}
```

- [ ] **Step 5: Register the tool**

In `mcp_server.py`, next to the `read_paragraphs_live` registration:

```python
        self.tools["get_outline_live"] = {
            "description": "List the headings of the active Writer document with the paragraph index of each",
            "parameters": {
                "type": "object",
                "properties": {}
            },
            "handler": self.get_outline_live
        }
        
```

and the handler next to `read_paragraphs_live`:

```python
    def get_outline_live(self) -> Dict[str, Any]:
        """List the headings of the active Writer document"""
        return self.uno_bridge.get_outline()
    
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_reading_tools.py -v`
Expected: PASS, 17 tests.

- [ ] **Step 7: Commit**

```bash
git add plugin/pythonpath/ tests/
git commit -m "Add get_outline_live

Turns a long document into a list of headings with addresses, which is what
lets an assistant navigate one it has not read. OutlineLevel is preferred so
custom styles with a level are picked up, with the Heading N style name as
the fallback."
```

---

## Task 5: `find_text_live`

**Files:**
- Modify: `plugin/pythonpath/uno_bridge.py`, `plugin/pythonpath/mcp_server.py`
- Modify: `tests/fake_writer.py` (search)
- Test: `tests/test_reading_tools.py` (append)

**Interfaces:**
- Consumes: `_locate_range` (Task 1), `_text_payload`, `_supports`.
- Produces: `UNOBridge.find_text(query, regex, case_sensitive, max_results, doc) -> dict`, `MAX_SEARCH_RESULTS`, tool `find_text_live`.

**Hit shape** (a refinement of the spec's looser wording — the address is nested so it can be passed straight back into a phase 2 tool):

```python
{"address": {"paragraph": 1, "offset": 7, "length": 9},
 "matched": "paragraph", "context": "Second paragraph here.",
 "context_truncated": False}
```

- [ ] **Step 1: Probe the real API**

```bash
cat > /tmp/probe_search.py <<'PY'
import subprocess, sys, time
import uno
PORT, PROFILE = 2012, "/tmp/probe_search_profile"
p = subprocess.Popen(["soffice", f"-env:UserInstallation=file://{PROFILE}",
                      "--headless", "--norestore", "--nologo", "--nodefault",
                      f"--accept=socket,host=127.0.0.1,port={PORT};urp;"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    local = uno.getComponentContext()
    r = local.ServiceManager.createInstanceWithContext("com.sun.star.bridge.UnoUrlResolver", local)
    for _ in range(60):
        try:
            ctx = r.resolve(f"uno:socket,host=127.0.0.1,port={PORT};urp;StarOffice.ComponentContext"); break
        except Exception: time.sleep(1)
    desktop = ctx.ServiceManager.createInstanceWithContext("com.sun.star.frame.Desktop", ctx)
    doc = desktop.loadComponentFromURL("private:factory/swriter", "_blank", 0, ())
    doc.getText().setString("alpha beta alpha gamma")
    d = doc.createSearchDescriptor()
    d.SearchString = "alpha"
    d.SearchRegularExpression = False
    d.SearchCaseSensitive = False
    found = doc.findAll(d)
    print("hits:", found.getCount())
    for i in range(found.getCount()):
        hit = found.getByIndex(i)
        print(" ", repr(hit.getString()), "owner is body:", hit.getText() is doc.getText())
    d.SearchString = "a(l|m)pha"
    d.SearchRegularExpression = True
    print("regex hits:", doc.findAll(d).getCount())
    doc.setModified(False); doc.close(True); desktop.terminate()
finally:
    time.sleep(2)
    if p.poll() is None: p.terminate()
PY
/usr/bin/python3 /tmp/probe_search.py
```

Expected: `hits: 2`, each hit printing `'alpha'`, and `regex hits: 2`. **If `createSearchDescriptor` or `findAll` is missing from the model**, stop and report — the tool needs a different mechanism (`XSearchable` on the text) and this task's design must change.

- [ ] **Step 2: Add search to the fake**

Append to `tests/fake_writer.py`:

```python
class FakeSearchDescriptor:
    """The subset of com.sun.star.util.SearchDescriptor the bridge sets."""

    SearchString = ""
    SearchRegularExpression = False
    SearchCaseSensitive = False


class FakeFindResults:
    def __init__(self, ranges):
        self._ranges = list(ranges)

    def getCount(self):
        return len(self._ranges)

    def getByIndex(self, index):
        return self._ranges[index]
```

and to `FakeWriterDoc`:

```python
    def createSearchDescriptor(self):
        return FakeSearchDescriptor()

    def findAll(self, descriptor):
        import re

        pattern = (descriptor.SearchString if descriptor.SearchRegularExpression
                   else re.escape(descriptor.SearchString))
        flags = 0 if descriptor.SearchCaseSensitive else re.IGNORECASE
        hits = []
        for index, paragraph in enumerate(self._text.paragraphs):
            for match in re.finditer(pattern, paragraph, flags):
                hits.append(FakeRange(self._text, (index, match.start()),
                                      (index, match.end())))
        return FakeFindResults(hits)
```

- [ ] **Step 3: Write the failing tests**

Append to `tests/test_reading_tools.py`:

```python
SEARCH_PARAGRAPHS = ["Alpha beta alpha.", "Gamma delta.", "ALPHA again."]


def test_finds_every_match_with_an_address(bridge):
    doc = writer_doc(SEARCH_PARAGRAPHS, caret=(0, 0))

    result = bridge.find_text("alpha", doc=doc)

    assert result["success"] is True
    assert result["total_hits"] == 3
    assert result["hits"][0]["address"] == {"paragraph": 0, "offset": 0, "length": 5}
    assert result["hits"][1]["address"] == {"paragraph": 0, "offset": 11, "length": 5}
    assert result["hits"][2]["address"] == {"paragraph": 2, "offset": 0, "length": 5}


def test_includes_the_matched_text_and_its_paragraph_as_context(bridge):
    doc = writer_doc(SEARCH_PARAGRAPHS, caret=(0, 0))

    hit = bridge.find_text("delta", doc=doc)["hits"][0]

    assert hit["matched"] == "delta"
    assert hit["context"] == "Gamma delta."
    assert hit["context_truncated"] is False


def test_honours_case_sensitivity(bridge):
    doc = writer_doc(SEARCH_PARAGRAPHS, caret=(0, 0))

    result = bridge.find_text("alpha", case_sensitive=True, doc=doc)

    # Only the lowercase occurrence in "Alpha beta alpha.", not "Alpha" or "ALPHA"
    assert result["total_hits"] == 1
    assert result["hits"][0]["address"] == {"paragraph": 0, "offset": 11, "length": 5}


def test_searches_by_regular_expression(bridge):
    doc = writer_doc(SEARCH_PARAGRAPHS, caret=(0, 0))

    result = bridge.find_text("g[a-z]+a", regex=True, doc=doc)

    assert [h["matched"] for h in result["hits"]] == ["Gamma"]


def test_caps_the_hits_but_reports_the_true_total(bridge):
    doc = writer_doc(["hit " * 40], caret=(0, 0))

    result = bridge.find_text("hit", max_results=5, doc=doc)

    assert len(result["hits"]) == 5
    assert result["total_hits"] == 40
    assert result["truncated"] is True


def test_reports_no_hits_without_failing(bridge):
    doc = writer_doc(SEARCH_PARAGRAPHS, caret=(0, 0))

    result = bridge.find_text("nowhere", doc=doc)

    assert result["success"] is True
    assert result["hits"] == []
    assert result["total_hits"] == 0


def test_rejects_an_empty_query(bridge):
    doc = writer_doc(SEARCH_PARAGRAPHS, caret=(0, 0))

    result = bridge.find_text("", doc=doc)

    assert result["success"] is False
    assert "query" in result["error"].lower()


def test_a_hit_address_resolves_back_to_the_matched_text(bridge):
    doc = writer_doc(SEARCH_PARAGRAPHS, caret=(0, 0))

    hit = bridge.find_text("delta", doc=doc)["hits"][0]
    resolved = bridge._resolve_address(doc, hit["address"])

    assert resolved.getString() == hit["matched"]


def test_find_text_tool_is_registered_and_dispatches():
    from mcp_server import LibreOfficeMCPServer

    server = LibreOfficeMCPServer()
    doc = writer_doc(SEARCH_PARAGRAPHS, caret=(0, 0))
    server.uno_bridge.get_active_document = lambda: doc

    assert "find_text_live" in server.tools

    result = asyncio.run(server.execute_tool("find_text_live", {"query": "delta"}))

    assert result["hits"][0]["matched"] == "delta"
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `uv run pytest tests/test_reading_tools.py -v -k "find or hit or query or regular"`
Expected: FAIL with `AttributeError: 'UNOBridge' object has no attribute 'find_text'`

- [ ] **Step 5: Implement**

Add next to `MAX_OUTLINE_ENTRIES`:

```python
# Search result caps for find_text
DEFAULT_SEARCH_RESULTS = 50
MAX_SEARCH_RESULTS = 200
```

Add to `UNOBridge`, after `get_outline`:

```python
    def find_text(self, query: str, regex: bool = False,
                  case_sensitive: bool = False,
                  max_results: int = DEFAULT_SEARCH_RESULTS,
                  doc: Any = None) -> Dict[str, Any]:
        """
        Find text in the active Writer document

        Each hit carries an address that resolves back to the match, so a hit
        can be handed straight to a tool that edits it, plus the containing
        paragraph as context. total_hits is the real number of matches even
        when the list is capped.
        """
        try:
            if doc is None:
                doc = self.get_active_document()

            if not doc:
                return {"success": False, "error": "No document available"}

            if not _supports(doc, WRITER_SERVICE):
                return {
                    "success": False,
                    "error": f"Searching is only available for Writer documents, "
                             f"got {self._get_document_type(doc)}"
                }

            if not isinstance(query, str) or not query:
                return {"success": False, "error": "query must be a non-empty string"}

            limit = max(1, min(int(max_results), MAX_SEARCH_RESULTS))

            descriptor = doc.createSearchDescriptor()
            descriptor.SearchString = query
            descriptor.SearchRegularExpression = bool(regex)
            descriptor.SearchCaseSensitive = bool(case_sensitive)

            found = doc.findAll(descriptor)
            total = found.getCount()

            hits = []
            for position in range(min(total, limit)):
                match = found.getByIndex(position)
                address, paragraph_cursor, _ = self._locate_range(doc, match)
                context = _text_payload(paragraph_cursor.getString())
                hits.append({
                    "address": address,
                    "matched": match.getString(),
                    "context": context["text"],
                    "context_truncated": context["truncated"]
                })

            logger.info(f"Found {total} matches for {query!r}, returning {len(hits)}")
            return {
                "success": True,
                "hits": hits,
                "total_hits": total,
                "truncated": total > len(hits)
            }

        except Exception as e:
            logger.error(f"Failed to search: {e}")
            return {"success": False, "error": str(e)}
```

- [ ] **Step 6: Register the tool**

In `mcp_server.py`, next to the other reading registrations:

```python
        self.tools["find_text_live"] = {
            "description": "Find text in the active Writer document, returning an address for each match that other tools can act on",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Text or regular expression to search for"
                    },
                    "regex": {
                        "type": "boolean",
                        "description": "Treat the query as a regular expression",
                        "default": False
                    },
                    "case_sensitive": {
                        "type": "boolean",
                        "description": "Match case exactly",
                        "default": False
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "How many matches to return (max 200)",
                        "default": 50
                    }
                },
                "required": ["query"]
            },
            "handler": self.find_text_live
        }
        
```

and the handler:

```python
    def find_text_live(self, query: str, regex: bool = False,
                       case_sensitive: bool = False,
                       max_results: int = 50) -> Dict[str, Any]:
        """Find text in the active Writer document"""
        return self.uno_bridge.find_text(query, regex=regex,
                                         case_sensitive=case_sensitive,
                                         max_results=max_results)
    
```

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest tests/ -v`
Expected: PASS for everything except the pre-existing `tests/test_client.py::test_mcp_client`, which fails because `pytest-asyncio` is not enabled. Total: 69 passed, 1 failed — 20 in `test_cursor_info.py`, 15 in `test_address.py`, 26 in `test_reading_tools.py`, 6 in `test_document_type_dispatch.py`, 2 in `test_insert_fix.py`.

- [ ] **Step 8: Commit**

```bash
git add plugin/pythonpath/ tests/
git commit -m "Add find_text_live

Search with an address per hit, so a match can be handed straight to a tool
that edits it, plus the containing paragraph as context. total_hits reports
the real count when the list is capped."
```

---

## Task 6: Live harness

The fakes encode assumptions; this is what checks them. It replaces the throwaway scripts with something committed and rerunnable, and phase 2 extends it.

**Files:**
- Create: `tests/live/writer_tools_check.py`

**Interfaces:**
- Consumes: everything from Tasks 1–5.
- Produces: a runnable script exiting non-zero on any failure.

- [ ] **Step 1: Write the harness**

Create `tests/live/writer_tools_check.py`:

```python
"""Check the Writer tools against a real LibreOffice.

Runs its own headless instance with a separate user profile, so a developer's
session is untouched. Must run under /usr/bin/python3, which carries the
python3-uno bindings; the repo venv has no uno module:

    /usr/bin/python3 tests/live/writer_tools_check.py

The fakes in tests/fake_writer.py encode assumptions about UNO. This checks
them. Anything unverified here is not known to work.
"""

import os
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "plugin", "pythonpath"))

import uno  # noqa: E402
from com.sun.star.text.ControlCharacter import PARAGRAPH_BREAK  # noqa: E402

PORT = 2010
PROFILE = "/tmp/mcp_live_check_profile"
failures = []


def check(label, actual, expected):
    ok = actual == expected
    print(f"{'PASS' if ok else 'FAIL'}  {label}: {actual!r}"
          + ("" if ok else f" (expected {expected!r})"))
    if not ok:
        failures.append(label)


def connect():
    local = uno.getComponentContext()
    resolver = local.ServiceManager.createInstanceWithContext(
        "com.sun.star.bridge.UnoUrlResolver", local)
    url = (f"uno:socket,host=127.0.0.1,port={PORT};urp;"
           "StarOffice.ComponentContext")
    for _ in range(60):
        try:
            return resolver.resolve(url)
        except Exception:
            time.sleep(1)
    raise RuntimeError("could not connect to headless soffice")


def build_document(desktop):
    """Headings, body text and a table, so every code path is exercised."""
    doc = desktop.loadComponentFromURL("private:factory/swriter", "_blank", 0, ())
    text = doc.getText()
    cursor = text.createTextCursor()
    for style, body in [
        ("Heading 1", "Chapter One"),
        ("Standard", "Alpha beta alpha."),
        ("Heading 2", "Section A"),
        ("Standard", "Gamma delta."),
    ]:
        cursor.ParaStyleName = style
        text.insertString(cursor, body, False)
        text.insertControlCharacter(cursor, PARAGRAPH_BREAK, False)

    table = doc.createInstance("com.sun.star.text.TextTable")
    table.initialize(2, 2)
    text.insertTextContent(cursor, table, False)
    table.getCellByName("A1").setString("in cell")
    return doc


soffice = subprocess.Popen([
    "soffice", f"-env:UserInstallation=file://{PROFILE}",
    "--headless", "--norestore", "--nologo", "--nodefault",
    f"--accept=socket,host=127.0.0.1,port={PORT};urp;",
], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

try:
    ctx = connect()
    desktop = ctx.ServiceManager.createInstanceWithContext(
        "com.sun.star.frame.Desktop", ctx)
    doc = build_document(desktop)

    from uno_bridge import UNOBridge
    bridge = UNOBridge.__new__(UNOBridge)  # no local desktop wanted

    print("--- get_outline ---")
    outline = bridge.get_outline(doc)
    print(outline)
    check("outline success", outline.get("success"), True)
    check("heading texts", [h["text"] for h in outline["headings"]],
          ["Chapter One", "Section A"])
    check("heading levels", [h["level"] for h in outline["headings"]], [1, 2])
    check("heading paragraphs", [h["paragraph"] for h in outline["headings"]], [0, 2])

    print("\n--- read_paragraphs ---")
    window = bridge.read_paragraphs(start=1, count=2, doc=doc)
    print(window)
    check("window texts", [p["text"] for p in window["paragraphs"]],
          ["Alpha beta alpha.", "Section A"])
    check("window indices", [p["paragraph"] for p in window["paragraphs"]], [1, 2])
    check("style of a heading", window["paragraphs"][1]["style"], "Heading 2")

    print("\n--- find_text ---")
    found = bridge.find_text("alpha", doc=doc)
    print(found)
    check("hit count", found.get("total_hits"), 2)
    check("first hit address", found["hits"][0]["address"],
          {"paragraph": 1, "offset": 0, "length": 5})
    check("hit context", found["hits"][0]["context"], "Alpha beta alpha.")

    print("\n--- find_text with a regular expression ---")
    regex_hits = bridge.find_text("g[a-z]+a", regex=True, doc=doc)
    check("regex matched", [h["matched"] for h in regex_hits["hits"]], ["Gamma"])

    print("\n--- address round trip: every hit resolves to what was found ---")
    for hit in bridge.find_text("alpha", doc=doc)["hits"]:
        resolved = bridge._resolve_address(doc, hit["address"])
        check(f"round trip {hit['address']}", resolved.getString(), hit["matched"])

    print("\n--- resolver against the whole paragraph and a slice ---")
    check("whole paragraph",
          bridge._resolve_address(doc, {"paragraph": 3}).getString(), "Gamma delta.")
    check("slice",
          bridge._resolve_address(
              doc, {"paragraph": 1, "offset": 6, "length": 4}).getString(), "beta")

    print("\n--- resolver rejects what it cannot address ---")
    from uno_bridge import AddressError
    for label, address in [("paragraph past the end", {"paragraph": 99}),
                           ("offset past the end", {"paragraph": 3, "offset": 500}),
                           ("neither key", {"offset": 1})]:
        try:
            bridge._resolve_address(doc, address)
            check(label, "no error", "AddressError")
        except AddressError as e:
            print(f"PASS  {label}: {e}")

    print("\n--- a table does not break paragraph numbering ---")
    window = bridge.read_paragraphs(doc=doc)
    print("paragraphs:", [(p["paragraph"], p["text"]) for p in window["paragraphs"]])
    # Four inserted paragraphs, plus whatever empty ones Writer leaves around the
    # table; the point is that the table itself is not counted as a paragraph.
    check("at least the four inserted paragraphs",
          window["total_paragraphs"] >= 4, True)
    check("no paragraph holds the table's text",
          any(p["text"] == "in cell" for p in window["paragraphs"]), False)

    doc.setModified(False)
    doc.close(True)
    desktop.terminate()
finally:
    time.sleep(2)
    if soffice.poll() is None:
        soffice.terminate()
        try:
            soffice.wait(timeout=15)
        except subprocess.TimeoutExpired:
            soffice.kill()

print("\n" + ("FAILURES: " + ", ".join(failures) if failures
              else "ALL LIVE CHECKS PASSED"))
sys.exit(1 if failures else 0)
```

- [ ] **Step 2: Run it**

Run: `/usr/bin/python3 tests/live/writer_tools_check.py`
Expected: `ALL LIVE CHECKS PASSED`.

A failure here beats a green pytest run: fix the **bridge**, then re-run both. If the fakes turn out to have modelled UNO wrongly, fix `tests/fake_writer.py` too, and add the case that would have caught it.

The paragraph-count check is deliberately a lower bound: Writer adds empty paragraphs around an inserted table, and how many is not worth pinning. What matters is that the table is not counted as a paragraph and its cell text never appears in the body window — both asserted.

- [ ] **Step 3: Commit**

```bash
git add tests/live/writer_tools_check.py
git commit -m "Add a live harness for the Writer reading tools

The fakes encode assumptions about UNO and cannot catch a wrong one, which
is how four tools once passed their tests while never working. This drives a
real headless instance and checks the outline, the paragraph window, search,
and that every search address resolves back to the text that was found."
```

---

## Task 7: Document targeting

The spec requires every tool to accept an optional `document`, because "the
active document" is a coin flip with two windows open and an assistant that
edits the wrong file is worse than one that refuses. This retrofits the three
reading tools; phase 2 tools take it from the start.

**Files:**
- Modify: `plugin/pythonpath/uno_bridge.py`, `plugin/pythonpath/mcp_server.py`
- Modify: `tests/fake_writer.py` (a desktop that enumerates documents)
- Test: `tests/test_document_targeting.py` (create)

**Interfaces:**
- Consumes: `get_active_document`, `self.desktop` (set in `UNOBridge.__init__`).
- Produces: `UNOBridge.document_for(url) -> doc|None`, `LibreOfficeMCPServer._target_document(document) -> (doc|None, error|None)`. Every phase 2 handler consumes the latter.

- [ ] **Step 1: Add a desktop to the fakes**

Append to `tests/fake_writer.py`:

```python
class FakeComponents:
    """What Desktop.getComponents() hands back: an enumeration of documents."""

    def __init__(self, documents):
        self._documents = list(documents)

    def createEnumeration(self):
        return FakeEnumeration(self._documents)


class FakeDesktop:
    def __init__(self, documents):
        self._documents = list(documents)

    def getComponents(self):
        return FakeComponents(self._documents)
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_document_targeting.py`:

```python
"""Tests for naming which open document a tool should act on."""

import asyncio

import pytest

from tests.fake_writer import FakeDesktop, writer_doc
from tests.uno_stubs import install_uno_stubs

install_uno_stubs()

from uno_bridge import UNOBridge  # noqa: E402

PARAGRAPHS = ["Alpha.", "Beta."]


@pytest.fixture
def bridge():
    return UNOBridge()


def test_falls_back_to_the_active_document(bridge):
    active = writer_doc(PARAGRAPHS, caret=(0, 0))
    bridge.get_active_document = lambda: active

    assert bridge.document_for(None) is active


def test_returns_the_document_matching_a_url(bridge):
    first = writer_doc(PARAGRAPHS, caret=(0, 0))
    first.Title = "first.odt"
    second = writer_doc(PARAGRAPHS, caret=(0, 0))
    second.Title = "second.odt"
    bridge.desktop = FakeDesktop([first, second])

    assert bridge.document_for("file:///tmp/second.odt") is second


def test_returns_nothing_for_a_url_that_is_not_open(bridge):
    only = writer_doc(PARAGRAPHS, caret=(0, 0))
    bridge.desktop = FakeDesktop([only])

    assert bridge.document_for("file:///tmp/absent.odt") is None


def test_a_tool_reads_the_document_it_was_told_to_read():
    from mcp_server import LibreOfficeMCPServer

    server = LibreOfficeMCPServer()
    active = writer_doc(["Active document."], caret=(0, 0))
    active.Title = "active.odt"
    other = writer_doc(["Other document."], caret=(0, 0))
    other.Title = "other.odt"
    server.uno_bridge.get_active_document = lambda: active
    server.uno_bridge.desktop = FakeDesktop([active, other])

    result = asyncio.run(server.execute_tool(
        "read_paragraphs_live", {"document": "file:///tmp/other.odt"}))

    assert result["paragraphs"][0]["text"] == "Other document."


def test_a_tool_reports_a_document_it_cannot_find():
    from mcp_server import LibreOfficeMCPServer

    server = LibreOfficeMCPServer()
    active = writer_doc(["Active document."], caret=(0, 0))
    server.uno_bridge.get_active_document = lambda: active
    server.uno_bridge.desktop = FakeDesktop([active])

    result = asyncio.run(server.execute_tool(
        "read_paragraphs_live", {"document": "file:///tmp/absent.odt"}))

    assert result["success"] is False
    assert "absent.odt" in result["error"]
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_document_targeting.py -v`
Expected: FAIL with `AttributeError: 'UNOBridge' object has no attribute 'document_for'`

- [ ] **Step 4: Implement the lookup**

Add to `UNOBridge`, right after `get_active_document`:

```python
    def document_for(self, url: Optional[str] = None) -> Any:
        """
        The document a tool should act on

        Without a url this is the active document. With one it is the open
        document whose URL matches, so a tool never silently acts on whichever
        window happens to be focused. Returns None when nothing matches.
        """
        if not url:
            return self.get_active_document()

        try:
            enumeration = self.desktop.getComponents().createEnumeration()
        except Exception as e:
            logger.error(f"Could not enumerate open documents: {e}")
            return None

        while enumeration.hasMoreElements():
            component = enumeration.nextElement()
            try:
                if component.getURL() == url:
                    return component
            except Exception:
                continue  # not a document, e.g. the Start Center
        logger.info(f"No open document with URL {url}")
        return None
```

- [ ] **Step 5: Wire the handlers**

In `mcp_server.py`, add to `LibreOfficeMCPServer` just above `get_cursor_info_live`:

```python
    def _target_document(self, document: Optional[str] = None) -> tuple:
        """(document, error) for a tool that may name a specific document"""
        doc = self.uno_bridge.document_for(document)
        if doc is None:
            if document:
                return None, {"success": False,
                              "error": f"No open document with URL {document}"}
            return None, {"success": False, "error": "No document available"}
        return doc, None
    
```

Replace the three reading handlers with versions that use it:

```python
    def read_paragraphs_live(self, start: int = 0, count: int = 50,
                             document: Optional[str] = None) -> Dict[str, Any]:
        """Read a window of paragraphs from a Writer document"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.read_paragraphs(start=start, count=count, doc=doc)
    
    def get_outline_live(self, document: Optional[str] = None) -> Dict[str, Any]:
        """List the headings of a Writer document"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.get_outline(doc=doc)
    
    def find_text_live(self, query: str, regex: bool = False,
                       case_sensitive: bool = False, max_results: int = 50,
                       document: Optional[str] = None) -> Dict[str, Any]:
        """Find text in a Writer document"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.find_text(query, regex=regex,
                                         case_sensitive=case_sensitive,
                                         max_results=max_results, doc=doc)
    
```

Then add this property to the `"properties"` object of all three registrations in `_register_tools`:

```python
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_document_targeting.py -v`
Expected: PASS, 5 tests.

Then the full suite — `uv run pytest tests/ -v` — 74 passed, 1 failed (the pre-existing `test_client.py`).

- [ ] **Step 7: Commit**

```bash
git add plugin/pythonpath/ tests/
git commit -m "Let the reading tools name their document

With two windows open, acting on the active document is a coin flip. Each
reading tool now takes an optional document URL from list_open_documents and
refuses a URL it cannot find, rather than silently reading the wrong file.

Not yet covered by the live harness: an unsaved document has no URL, so
checking this against a real instance needs a saved file, which phase 2 adds."
```

---

## Task 8: Documentation

**Files:**
- Modify: `CLAUDE.md`, `docs/LIBREOFFICE_MCP_EXTENSION.md`

- [ ] **Step 1: Update the plugin's tool count and the address note in CLAUDE.md**

Change the heading `### 2. Embedded plugin — plugin/pythonpath/ (9 tools, SSE over HTTP :8765)` to read **12 tools**, since phase 1 adds three, and add to the constraints list in that section:

```markdown
- Text is addressed one way: `{"paragraph": i, "offset": k, "length": n}` (body paragraphs, tables skipped) or `{"selection": true}`. `_locate_range` produces an address from a range, `_resolve_address` turns one back into a range, and every tool goes through them — see `docs/superpowers/specs/2026-08-18-writer-text-tools-design.md`.
- `tests/live/writer_tools_check.py` checks the bridge against a real headless LibreOffice. Run it with `/usr/bin/python3` (the venv has no `uno`) whenever bridge code changes; a green pytest run alone does not mean the UNO calls work.
```

- [ ] **Step 2: Add the tools to the extension guide**

In `docs/LIBREOFFICE_MCP_EXTENSION.md`, add before the `### **Cursor and Selection**` section:

```markdown
### **Reading and Search**
- `get_outline_live`: Headings with the paragraph index of each (Writer only)
- `read_paragraphs_live`: A window of paragraphs with indices and styles; `start`, `count` (max 200)
- `find_text_live`: Search by text or regular expression; each hit carries an address other tools can act on
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md docs/LIBREOFFICE_MCP_EXTENSION.md
git commit -m "Document the phase 1 reading tools"
```

---

## Done when

- `uv run pytest tests/ -v` — 74 passed, plus the pre-existing `test_client.py` failure.
- `/usr/bin/python3 tests/live/writer_tools_check.py` — `ALL LIVE CHECKS PASSED`.
- `cd plugin && ./install.sh install`, restart LibreOffice, and `get_outline_live` answers over SSE against a real document (the plugin's Python is only reloaded on restart).
