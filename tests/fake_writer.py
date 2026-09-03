"""A minimal fake of the UNO objects a Writer document exposes.

Only the handful of calls the bridge makes are modelled. A position is a
(paragraph_index, offset) tuple; a text cursor keeps a mark and a position,
which is how UNO's expand flag behaves: goto*(True) moves the position and
leaves the mark, so getString() spans the two.

These fakes encode an assumption about UNO's semantics, so they cannot catch a
wrong assumption — only a live LibreOffice can. They do pin down the offset and
index arithmetic, the selection handling, and the error paths.
"""

from tests.uno_stubs import install_uno_stubs

install_uno_stubs()

# Deliberately NOT subclassing the com.sun.star.* interfaces: a real UNO object
# is <class 'pyuno'> and satisfies isinstance against none of them, so faking
# documents that way would hide exactly the bug it should catch. Real code has
# to ask supportsService().
WRITER_SERVICES = frozenset({
    "com.sun.star.text.TextDocument",
    "com.sun.star.text.GenericTextDocument",
})
CALC_SERVICES = frozenset({"com.sun.star.sheet.SpreadsheetDocument"})


class FakeRange:
    def __init__(self, model, start, end=None):
        self.model = model
        self.start = start
        self.end = start if end is None else end

    def getString(self):
        return self.model.slice_text(self.start, self.end)

    def getText(self):
        return self.model

    def getStart(self):
        return FakeRange(self.model, self.start)

    def getEnd(self):
        return FakeRange(self.model, self.end)


class FakeTextCursor:
    """A text cursor: `mark` stays put, `pos` moves, getString() spans both."""

    def __init__(self, model, mark, pos):
        self.model = model
        self.mark = mark
        self.pos = pos

    @property
    def start(self):
        return min(self.mark, self.pos)

    @property
    def end(self):
        return max(self.mark, self.pos)

    def getString(self):
        return self.model.slice_text(self.start, self.end)

    def getText(self):
        return self.model

    def getStart(self):
        return FakeRange(self.model, self.start)

    def gotoStartOfParagraph(self, expand):
        self.pos = (self.pos[0], 0)
        if not expand:
            self.mark = self.pos
        return True

    def gotoEndOfParagraph(self, expand):
        self.pos = (self.pos[0], len(self.model.paragraphs[self.pos[0]]))
        if not expand:
            self.mark = self.pos
        return True

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


class FakeParagraph(FakeRange):
    def __init__(self, model, index):
        super().__init__(model, (index, 0), (index, len(model.paragraphs[index])))
        self.index = index
        self.ParaStyleName = model.styles[index]
        if model.expose_outline_level:
            self.OutlineLevel = model.outline_levels[index]


class FakeTextTable:
    """A table in the body enumeration: no getStart(), so the walk must skip it."""


class FakeEnumeration:
    def __init__(self, items):
        self._items = list(items)

    def hasMoreElements(self):
        return bool(self._items)

    def nextElement(self):
        return self._items.pop(0)


class FakeText:
    """Models com.sun.star.text.Text: cursor factory, enumeration, comparison."""

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

    def _own(self, text_range):
        """Writer throws when a range from another text is passed in."""
        if text_range.getText() is not self:
            raise RuntimeError(
                "End of content node doesn't have the proper start node")

    def slice_text(self, start, end):
        (start_para, start_offset), (end_para, end_offset) = sorted([start, end])
        if start_para == end_para:
            return self.paragraphs[start_para][start_offset:end_offset]
        parts = [self.paragraphs[start_para][start_offset:]]
        parts.extend(self.paragraphs[p] for p in range(start_para + 1, end_para))
        parts.append(self.paragraphs[end_para][:end_offset])
        return "\n".join(parts)

    def getString(self):
        return "\n".join(self.paragraphs)

    def createTextCursorByRange(self, text_range):
        self._own(text_range)
        return FakeTextCursor(self, text_range.start, text_range.start)

    def createEnumeration(self):
        return FakeEnumeration(
            FakeTextTable() if item == "table" else FakeParagraph(self, item)
            for item in self.enumeration_items
        )

    def compareRegionStarts(self, range1, range2):
        """0 when both start at the same spot; the sign convention is unused."""
        self._own(range1)
        self._own(range2)
        if range1.start == range2.start:
            return 0
        return 1 if range1.start < range2.start else -1


class FakeSelection:
    def __init__(self, model, spans):
        self._ranges = [FakeRange(model, start, end) for start, end in spans]

    def getCount(self):
        return len(self._ranges)

    def getByIndex(self, index):
        return self._ranges[index]


class FakeTableCellSelection:
    """What Writer hands back for a table cell selection: no ranges to read."""


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


class FakeViewCursor(FakeRange):
    def __init__(self, model, start, end=None, page=1):
        super().__init__(model, start, end)
        self.page = page

    def getPage(self):
        return self.page


class FakeController:
    def __init__(self, view_cursor, selection):
        self._view_cursor = view_cursor
        self._selection = selection

    def getViewCursor(self):
        return self._view_cursor

    def getSelection(self):
        return self._selection


class FakeDoc:
    """Common shape of a UNO document proxy."""

    services = frozenset()
    Title = "fake"

    def supportsService(self, name):
        return name in self.services

    def getURL(self):
        return f"file:///tmp/{self.Title}"

    def isModified(self):
        return False


class FakeWriterDoc(FakeDoc):
    services = WRITER_SERVICES
    Title = "fake.odt"

    def __init__(self, text, controller):
        self._text = text
        self._controller = controller

    def getText(self):
        return self._text

    def getCurrentController(self):
        return self._controller

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


class FakeCalcDoc(FakeDoc):
    services = CALC_SERVICES
    Title = "fake.ods"

    def getCurrentController(self):
        raise AssertionError("Calc documents must be rejected before this is used")


class FakeUnknownDoc(FakeDoc):
    """A component that answers no service query, e.g. a Base or Math document."""

    Title = "fake.odb"


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


def writer_doc_with_caret_in_cell(paragraphs, cell_paragraph, caret_offset, page=1):
    """Caret inside a table cell, whose text is separate from the body text."""
    body = FakeText(paragraphs)
    cell = FakeText([cell_paragraph])
    caret = (0, caret_offset)
    view_cursor = FakeViewCursor(cell, caret, caret, page=page)
    selection = FakeSelection(cell, [(caret, caret)])
    return FakeWriterDoc(body, FakeController(view_cursor, selection))


def writer_doc(paragraphs, caret, selection_spans=(), page=1, **text_kwargs):
    """Build a Writer document whose caret sits at `caret` = (paragraph, offset)."""
    text = FakeText(paragraphs, **text_kwargs)
    selection_end = caret
    if selection_spans:
        selection_end = max(max(span) for span in selection_spans)
    view_cursor = FakeViewCursor(text, caret, selection_end, page=page)
    selection = FakeSelection(text, selection_spans or [(caret, caret)])
    return FakeWriterDoc(text, FakeController(view_cursor, selection))
