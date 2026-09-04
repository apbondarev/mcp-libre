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


class FakeLocale:
    """com.sun.star.lang.Locale, as much of it as the bridge touches."""

    def __init__(self, language="en", country="US"):
        self.Language = language
        self.Country = country
        self.Variant = ""

    def __eq__(self, other):
        return (self.Language, self.Country) == (
            getattr(other, "Language", None), getattr(other, "Country", None))

    def __repr__(self):
        return f"FakeLocale({self.Language}-{self.Country})"


class FakeRange:
    def __init__(self, model, start, end=None):
        self.model = model
        self.start = start
        self.end = start if end is None else end

    @property
    def CharLocale(self):
        return self.model.locale_at(self.start)

    @CharLocale.setter
    def CharLocale(self, value):
        self.model.set_locale(self.start, self.end, value)

    def getString(self):
        return self.model.slice_text(self.start, self.end)

    def getText(self):
        return self.model

    def setString(self, value):
        self.model.replace_range(self.start, self.end, value)

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
    def CharLocale(self):
        return self.model.locale_at(self.start)

    @CharLocale.setter
    def CharLocale(self, value):
        self.model.set_locale(self.start, self.end, value)

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

    def setString(self, value):
        self.model.replace_range(self.start, self.end, value)

    def getStart(self):
        return FakeRange(self.model, self.start)

    def getEnd(self):
        return FakeRange(self.model, self.end)

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
        if model.expose_outline_level:
            self.OutlineLevel = model.outline_levels[index]

    @property
    def FillStyle(self):
        return self.model.fills.get(self.index, {}).get("FillStyle")

    @FillStyle.setter
    def FillStyle(self, value):
        self.model.fills.setdefault(self.index, {})["FillStyle"] = value

    @property
    def FillColor(self):
        return self.model.fills.get(self.index, {}).get("FillColor")

    @FillColor.setter
    def FillColor(self, value):
        self.model.fills.setdefault(self.index, {})["FillColor"] = value

    def createEnumeration(self):
        return FakeEnumeration(
            FakeTextPortion(text, locale, properties)
            for text, locale, properties in self.model.portions_of(self.index))


class FakeTextPortion:
    """A run inside a paragraph, carrying its own language and formatting."""

    DEFAULTS = {"CharWeight": 100.0, "CharPosture": "NONE", "CharUnderline": 0,
                "CharHeight": 12.0, "CharFontName": "Liberation Serif",
                "CharColor": -1, "CharBackColor": -1, "HyperLinkURL": "",
                "HyperLinkTarget": "", "CharStyleName": ""}

    def __init__(self, text, locale, properties=None):
        self._text = text
        self.CharLocale = locale
        for name, value in dict(self.DEFAULTS, **(properties or {})).items():
            setattr(self, name, value)

    def getString(self):
        return self._text


class FakeSpellChecker:
    """Stands in for com.sun.star.linguistic2.SpellChecker.

    Knows a fixed vocabulary per language, so a test can say what is a word
    and what is not without shipping a dictionary.
    """

    def __init__(self, vocabulary=None, suggestions=None, locales=("ru-RU", "en-US")):
        self.vocabulary = vocabulary or {}
        self.suggestions = suggestions or {}
        self.locales = set(locales)
        self.checked = []

    def hasLocale(self, locale):
        return f"{locale.Language}-{locale.Country}" in self.locales

    def isValid(self, word, locale, properties):
        tag = f"{locale.Language}-{locale.Country}"
        self.checked.append((word, tag))
        return word in self.vocabulary.get(tag, ())

    def spell(self, word, locale, properties):
        if self.isValid(word, locale, properties):
            return None
        return FakeSpellAlternatives(self.suggestions.get(word, []))


class FakeSpellAlternatives:
    def __init__(self, alternatives):
        self._alternatives = list(alternatives)

    def getAlternatives(self):
        return tuple(self._alternatives)


def _char_property(name):
    """A character property that records what was applied, for assertions."""

    def getter(self):
        return self.model.char_property(self.start, self.end, name)

    def setter(self, value):
        self.model.record_char_property(self.start, self.end, name, value)

    return property(getter, setter)


BORDER_PROPERTIES = ("TopBorder", "BottomBorder", "LeftBorder", "RightBorder",
                     "TopBorderDistance", "BottomBorderDistance",
                     "LeftBorderDistance", "RightBorderDistance")


def _border_property(name):
    """A paragraph border side, recorded per span for assertions."""

    def getter(self):
        return self.model.border_property(self.start, self.end, name)

    def setter(self, value):
        self.model.record_border_property(self.start, self.end, name, value)

    return property(getter, setter)


def _para_style_property():
    def getter(self):
        return self.model.styles[self.start[0]]

    def setter(self, value):
        self.model.set_style(self.start, self.end, value)

    return property(getter, setter)


for _range_type in (FakeRange, FakeTextCursor):
    for _property_name in ("CharWeight", "CharPosture", "CharUnderline",
                           "CharHeight", "CharFontName", "CharColor",
                           "CharBackColor", "HyperLinkURL", "HyperLinkTarget",
                           "CharStyleName", "UnvisitedCharStyleName",
                           "VisitedCharStyleName"):
        setattr(_range_type, _property_name, _char_property(_property_name))
    for _border_name in BORDER_PROPERTIES:
        setattr(_range_type, _border_name, _border_property(_border_name))
    setattr(_range_type, "ParaStyleName", _para_style_property())


class FakeStyleFamily:
    def __init__(self, names):
        self.names = list(names)

    def hasByName(self, name):
        return name in self.names

    def getElementNames(self):
        return tuple(self.names)


class FakeStyleFamilies:
    """doc.StyleFamilies, with the families a Writer document has."""

    def __init__(self, families=None):
        self.families = families or {
            "ParagraphStyles": ["Standard", "Text body", "Heading 1", "Heading 2",
                                "Preformatted Text", "Quotations"],
            "CharacterStyles": ["Default Style", "Emphasis", "Source Text"],
        }

    def getElementNames(self):
        return tuple(self.families)

    def hasByName(self, name):
        return name in self.families

    def getByName(self, name):
        if name not in self.families:
            raise RuntimeError(f"no style family {name}")
        return FakeStyleFamily(self.families[name])


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
                 outline_levels=None, expose_outline_level=True,
                 default_locale=None, portions=None):
        self.paragraphs = list(paragraphs)
        self.styles = list(styles) if styles else ["Standard"] * len(self.paragraphs)
        self.outline_levels = (list(outline_levels) if outline_levels
                               else [0] * len(self.paragraphs))
        self.expose_outline_level = expose_outline_level
        self.default_locale = default_locale or ("en", "US")
        self.char_formatting = []
        self.border_formatting = []
        self.fills = {}
        self.portions = dict(portions) if portions else {}
        self.enumeration_items = (
            list(range(len(self.paragraphs)))
            if enumeration_items is None
            else list(enumeration_items)
        )

    def record_border_property(self, start, end, name, value):
        self.border_formatting.append({"span": (start, end), name: value})

    def border_property(self, start, end, name):
        for applied in reversed(self.border_formatting):
            if applied["span"] == (start, end) and name in applied:
                return applied[name]
        return None

    def record_char_property(self, start, end, name, value):
        """Remember a character property applied to a span."""
        self.char_formatting.append({"span": (start, end), name: value})

    def char_property(self, start, end, name):
        """The last value applied to this span for a property, else None."""
        for applied in reversed(self.char_formatting):
            if applied["span"] == (start, end) and name in applied:
                return applied[name]
        return None

    def set_style(self, start, end, style):
        (start_para, _), (end_para, _) = sorted([start, end])
        for paragraph in range(start_para, end_para + 1):
            self.styles[paragraph] = style

    def locale_at(self, position):
        """The locale of the portion holding a position."""
        paragraph, offset = position
        for text, locale, _ in self.portions_of(paragraph):
            if offset < len(text) or (offset == len(text) and len(text)):
                return locale
            offset -= len(text)
        return FakeLocale(*self.default_locale)

    def set_locale(self, start, end, locale):
        """
        Mark exactly the span with one locale, splitting runs at its edges

        Writer marks characters, not paragraphs, so a fake that marked whole
        paragraphs would hide the difference between tagging a phrase and
        tagging everything around it.
        """
        (start_para, start_offset), (end_para, end_offset) = sorted([start, end])
        for paragraph in range(start_para, end_para + 1):
            body = self.paragraphs[paragraph]
            from_offset = start_offset if paragraph == start_para else 0
            to_offset = end_offset if paragraph == end_para else len(body)
            rebuilt, position = [], 0
            for text, existing, properties in self.portions_of(paragraph):
                for character, index in zip(text, range(position, position + len(text))):
                    marked = from_offset <= index < to_offset
                    chosen = locale if marked else existing
                    if rebuilt and rebuilt[-1][1] is chosen \
                            and rebuilt[-1][2] == properties:
                        rebuilt[-1] = (rebuilt[-1][0] + character, chosen, properties)
                    else:
                        rebuilt.append((character, chosen, properties))
                position += len(text)
            self.portions[paragraph] = [
                {"text": text, "locale": marked_locale, **properties}
                for text, marked_locale, properties in rebuilt]

    def portions_of(self, paragraph):
        """
        The runs of a paragraph, one run unless told otherwise

        A run is either a (text, locale) pair or a dict with text, locale and
        whatever character properties the test cares about.
        """
        declared = self.portions.get(paragraph)
        if declared is None:
            return [(self.paragraphs[paragraph],
                     FakeLocale(*self.default_locale), {})]
        normalised = []
        for run in declared:
            if isinstance(run, dict):
                properties = {k: v for k, v in run.items()
                              if k not in ("text", "locale")}
                normalised.append((run["text"],
                                   run.get("locale",
                                           FakeLocale(*self.default_locale)),
                                   properties))
            else:
                text, locale = run
                normalised.append((text, locale, {}))
        return normalised

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

    def replace_range(self, start, end, value):
        """
        Rewrite the span, joining paragraphs when the span crosses a break

        Declared portions for the paragraphs touched are dropped: they
        described the old text, and keeping them would let a test read back
        runs that no longer exist. What the new text looks like is a question
        only a live LibreOffice answers, so the read-after-write round trip is
        checked in tests/live/writer_tools_check.py instead.
        """
        (first, _), (last, _) = sorted([start, end])
        for paragraph in range(first, last + 1):
            self.portions.pop(paragraph, None)
        (start_para, start_offset), (end_para, end_offset) = sorted([start, end])
        if start_para == end_para:
            paragraph = self.paragraphs[start_para]
            self.paragraphs[start_para] = (
                paragraph[:start_offset] + value + paragraph[end_offset:])
            return
        head = self.paragraphs[start_para][:start_offset]
        tail = self.paragraphs[end_para][end_offset:]
        self.paragraphs[start_para:end_para + 1] = [head + value + tail]
        del self.styles[start_para + 1:end_para + 1]
        del self.outline_levels[start_para + 1:end_para + 1]
        self.enumeration_items = list(range(len(self.paragraphs)))

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


class FakeUndoManager:
    """Records the undo contexts a tool opens and closes."""

    def __init__(self):
        self.calls = []

    def enterUndoContext(self, title):
        self.calls.append(("enter", title))

    def leaveUndoContext(self):
        self.calls.append(("leave", None))


class FakeRedlines:
    """doc.getRedlines(): the recorded changes awaiting acceptance."""

    def __init__(self, count=0):
        self.count = count

    def getCount(self):
        return self.count


class FakeDoc:
    """Common shape of a UNO document proxy."""

    services = frozenset()
    Title = "fake"
    RecordChanges = False
    readonly = False

    def isReadonly(self):
        return self.readonly

    def getRedlines(self):
        return FakeRedlines(getattr(self, "redline_count", 0))

    @property
    def StyleFamilies(self):
        return FakeStyleFamilies()

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
        self.UndoManager = FakeUndoManager()

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


class FakeModalDialog:
    """What getCurrentComponent() hands back while a dialog has the focus.

    LibreOffice's current component follows the focused frame, so a modal
    dialog or the Start Center answers here instead of the document. It carries
    no Title and supports no document service.
    """

    def supportsService(self, name):
        return False


class FakeDesktop:
    def __init__(self, documents, current=None):
        self._documents = list(documents)
        self._current = current if current is not None else (
            documents[0] if documents else None)

    def getCurrentComponent(self):
        return self._current

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
