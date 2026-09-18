"""A minimal fake of the UNO objects a Writer document exposes.

Only the handful of calls the bridge makes are modelled. A position is a
(paragraph_index, offset) tuple; a text cursor keeps a mark and a position,
which is how UNO's expand flag behaves: goto*(True) moves the position and
leaves the mark, so getString() spans the two.

These fakes encode assumptions about UNO's semantics, so they cannot prove
those assumptions — only tests/live/writer_tools_check.py can, against a real
LibreOffice. Where a fake was too kind, the live checks caught it and the
fake was made faithful; those places carry a comment saying what was
measured.

This file holds the document itself and the factories tests build with, and
takes the rest back in so every test keeps importing from one place:

    fakes_values.py       locales, enums, sizes, structs, the spell checker
    fakes_text.py         ranges, cursors, paragraphs, portions, the text
    fakes_tables.py       tables and their cells
    fakes_annotations.py  comments and pictures
    fakes_styles.py       styles that inherit as real ones do
"""

import itertools
from tests.uno_stubs import install_uno_stubs
from tests.fakes_tables import (FakeCell, FakeCellCursor, FakeTableBorder,
                                FakeTextTable, _cell_text)
from tests.fakes_text import (BORDER_PROPERTIES, FakeParagraph, FakeRange,
                              FakeText, FakeTextCursor, FakeTextPortion,
                              _border_property, _char_property,
                              _insert_text_content, _para_style_property)
from tests.fakes_values import (CALC_SERVICES, FakeBorderLine, FakeComponents,
                                FakeCount, FakeDateTime, FakeEnum,
                                FakeEnumeration, FakeGraphicProviderPNG,
                                FakeLineSpacing, FakeLocale, FakeNameAccess,
                                FakeProperties, FakeProperty, FakeSeparator,
                                FakeSize, FakeSpellAlternatives,
                                FakeSpellChecker, WRITER_SERVICES)
from tests.fakes_styles import (FAKE_STYLE_DEFAULTS, FAKE_STYLE_OWN,
                                FAKE_STYLE_PARENTS, FakeStyle, FakeStyleFamilies,
                                FakeStyleFamily)
from tests.fakes_references import (FakeFieldMaster, FakeReferenceField,
                                   FakeSequenceField)
from tests.fakes_indexes import (FakeDocumentIndex, FakeIndexMark,
                                 SERVICE_OF_KIND)
from tests.fakes_notes import FakeNote
from tests.fakes_pagestyles import FakeLineNumbering
from tests.fakes_sections import FakeColumns, FakeTextSection
from tests.fakes_annotations import (FakeAnnotation, FakeBookmark, FakeField,
                                     FakeGraphic, FakeImage,
                                     FakeNoteCursor, FakeNoteParagraph,
                                     FakeNoteText)

install_uno_stubs()





















































for _range_type in (FakeRange, FakeTextCursor):
    setattr(_range_type, "insertTextContent", _insert_text_content)
    for _property_name in ("CharWeight", "CharPosture", "CharUnderline",
                           "CharHeight", "CharFontName", "CharColor",
                           "CharBackColor", "HyperLinkURL", "HyperLinkTarget",
                           "CharStyleName", "UnvisitedCharStyleName",
                           "VisitedCharStyleName"):
        setattr(_range_type, _property_name, _char_property(_property_name))
    for _border_name in BORDER_PROPERTIES:
        setattr(_range_type, _border_name, _border_property(_border_name))
    setattr(_range_type, "ParaStyleName", _para_style_property())





































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
    """The view cursor, which is also where Writer keeps the page number.

    Jumping it to a page is how a page is rendered: the PNG filter renders
    the page the view is on, and the FilterData's page properties are
    ignored — measured on a live LibreOffice.
    """

    def __init__(self, model, start, end=None, page=1, pages=1):
        super().__init__(model, start, end)
        self.page = page
        self.pages = pages
        self.jumps = []
        # A real view cursor names the page style it stands on, which is how
        # a header tool knows which style a caller means.
        self.PageStyleName = "Standard"

    def getPage(self):
        return self.page

    def jumpToPage(self, page):
        self.jumps.append(page)
        if 1 <= page <= self.pages:
            self.page = page
            return True
        return False

    def gotoRange(self, other, expand):
        """Move the view cursor, keeping the mark when asked to expand.

        `expand` is what turns two calls into a selection — go to the start
        without it, then to the end with it — and a fake that ignored it left
        the view cursor collapsed at the end, so a command sent to the view
        acted on nothing.
        """
        if getattr(other, "model", None) is not self.model:
            raise RuntimeError(
                "End of content node doesn't have the proper start node")
        reach = other.end if hasattr(other, "end") else other.start
        if expand:
            self.end = reach
        else:
            self.start = other.start
            self.end = reach
        # A cursor sent into the text is on the page that text is on; the
        # fake keeps it simple and says the paragraph's index decides.
        self.page = min(self.pages, 1 + self.start[0] // 2)
        return True


class FakeFrame:
    """What a dispatch is sent to; it knows the document behind the view."""

    def __init__(self, document=None):
        self.document = document


class FakeController:
    def __init__(self, view_cursor, selection):
        self._view_cursor = view_cursor
        self._selection = selection
        self.frame = FakeFrame()

    def getFrame(self):
        return self.frame

    def select(self, text_range):
        """Selecting is what a reader does with the mouse: the view moves."""
        self._selection = FakeSelection(text_range.model,
                                        [(text_range.start, text_range.end)])
        return True

    def getViewCursor(self):
        return self._view_cursor

    def getSelection(self):
        return self._selection

    # -- copying, through the view rather than the system clipboard -----
    #
    # Measured: the controller's own transferable carries a paragraph with
    # its comments and its formatting, and the reader's clipboard is never
    # touched.

    def getTransferable(self):
        model = self._view_cursor.model
        first, last = self._view_cursor.start[0], self._view_cursor.end[0]
        return [{"text": model.paragraphs[index],
                 "style": model.styles[index],
                 "level": model.outline_levels[index],
                 "portions": model.portions.get(index)}
                for index in range(first, last + 1)]

    def insertTransferable(self, carried):
        model = self._view_cursor.model
        at = self._view_cursor.start[0]
        for offset, piece in enumerate(carried):
            model.put_paragraph(at + offset, dict(piece))
        return True


class FakeUndoManager:
    """Records the undo contexts a tool opens, and can take an edit back.

    Faithful to what was measured on a real Writer: contexts nest and only
    the outermost becomes an entry, a context that wrote nothing leaves no
    entry at all, and one undo takes back everything the outer context held.
    """

    # Writer keeps a limited number of undo steps and drops the oldest, so a
    # new entry on a full stack does not make the stack longer — measured,
    # after a failed batch went untaken-back because the count had not moved.
    limit = 100

    def __init__(self, text=None):
        self.calls = []
        self.text = text
        self.entries = []
        self._depth = 0
        self._snapshot = None
        self._title = ""

    def _state(self):
        if self.text is None:
            return None
        return (list(self.text.paragraphs), list(self.text.styles),
                list(self.text.outline_levels),
                {key: list(value) for key, value in self.text.portions.items()})

    def _restore(self, state):
        paragraphs, styles, levels, portions = state
        self.text.paragraphs[:] = paragraphs
        self.text.styles[:] = styles
        self.text.outline_levels[:] = levels
        self.text.portions.clear()
        self.text.portions.update(portions)

    def enterUndoContext(self, title):
        self.calls.append(("enter", title))
        if self._depth == 0:
            self._snapshot = self._state()
            self._title = title            # the outer one is what is seen
        self._depth += 1

    def leaveUndoContext(self):
        self.calls.append(("leave", None))
        self._depth = max(0, self._depth - 1)
        if self._depth or self._snapshot is None:
            return
        if self._state() != self._snapshot:      # nothing written, no entry
            self.entries.append((self._title, self._snapshot))
            del self.entries[:-self.limit]
        self._snapshot = None

    def getAllUndoActionTitles(self):
        return tuple(title for title, _ in reversed(self.entries))

    def undo(self):
        if not self.entries:
            raise RuntimeError("nothing to undo")
        _title, state = self.entries.pop()
        self._restore(state)


class FakeRedline:
    """One recorded change, as UNO hands it over: a property set and a range.

    Its own getString() **throws** on a real Writer
    (`unoredline.cxx:531`), so the text of a change is only readable by
    walking a cursor from RedlineStart to RedlineEnd — and this fake refuses
    it the same way, or nothing would ever exercise that walk.
    """

    _serial = itertools.count(1)

    def __init__(self, model, paragraph, start, end, kind="Insert",
                 author="Reviewer", description=None, comment=""):
        self.RedlineType = kind
        self.RedlineAuthor = author
        self.RedlineComment = comment
        self.RedlineDescription = (description
                                   or f"{kind} “{model.paragraphs[paragraph][start:end]}”")
        self.RedlineIdentifier = str(next(FakeRedline._serial) * 1000)
        self.RedlineDateTime = FakeDateTime()
        self.RedlineStart = FakeRange(model, (paragraph, start))
        self.RedlineEnd = FakeRange(model, (paragraph, end))

    def getString(self):
        raise RuntimeError("at ./sw/source/core/unocore/unoredline.cxx:531")


class FakeRedlines:
    """doc.getRedlines(): the recorded changes awaiting acceptance."""

    def __init__(self, count=0, entries=None):
        self.entries = list(entries) if entries is not None else []
        self.count = count if entries is None else len(self.entries)

    def getCount(self):
        return len(self.entries) if self.entries else self.count

    def getByIndex(self, index):
        return self.entries[index]


# The services an index is created under, and which kind each one is.
_INDEX_SERVICES = {
    "ContentIndex": "contents", "DocumentIndex": "alphabetical",
    "IllustrationsIndex": "illustrations", "TableIndex": "tables",
    "ObjectIndex": "objects", "UserIndex": "user",
    "Bibliography": "bibliography",
}


class FakeIndexed:
    """com.sun.star.container.XIndexAccess: what getFootnotes() hands back.

    Notes have no names in UNO, only positions — which is why a note here is
    named by where its mark sits.
    """

    def __init__(self, items):
        self.items = list(items)

    def getCount(self):
        return len(self.items)

    def getByIndex(self, index):
        return self.items[index]


class FakeMasters:
    """The field masters, reachable by the long service-style name.

    `getElementNames` spells them `fieldmaster` in lower case while
    `getByName` takes the `FieldMaster` spelling — measured on a real
    document, and the reason nothing here compares the two.
    """

    def __init__(self, by_name):
        self.by_name = by_name

    @staticmethod
    def _category(name):
        return name.rsplit(".", 1)[-1]

    def getElementNames(self):
        return tuple("com.sun.star.text.fieldmaster.SetExpression." + name
                     for name in self.by_name)

    def hasByName(self, name):
        return self._category(name) in self.by_name

    def getByName(self, name):
        return self.by_name[self._category(name)]


class FakeDoc:
    """Common shape of a UNO document proxy."""

    services = frozenset()
    Title = "fake"
    RecordChanges = False
    readonly = False
    _runtime_uids = iter(range(1, 10000))

    @property
    def RuntimeUID(self):
        """The document's own id while it is open — '1', '2', …

        Measured on a real office, where two open documents answer with
        different ones. Anchors are kept per document by this, since two
        untitled documents share everything else.
        """
        uid = getattr(self, "_runtime_uid", None)
        if uid is None:
            uid = str(next(FakeDoc._runtime_uids))
            self._runtime_uid = uid
        return uid

    def isReadonly(self):
        return self.readonly

    def getBookmarks(self):
        """The document's own names for places, by name."""
        if not hasattr(self, "_bookmarks"):
            self._bookmarks = FakeNameAccess([])
            self._text.bookmarks = self._bookmarks.items
        return self._bookmarks

    _sequence_ids = iter(range(0, 100000))

    def getTextFieldMasters(self):
        """The sequences a caption's number can count in.

        A fresh Writer document already carries five of them, and the element
        names come back spelled `fieldmaster` in lower case while getByName
        takes the `FieldMaster` spelling — measured, and modelled here.
        """
        if not hasattr(self, "_masters"):
            self._masters = {
                name: FakeFieldMaster(name)
                for name in ("Illustration", "Table", "Text", "Drawing",
                             "Figure")}
        return FakeMasters(self._masters)

    def getReferenceMarks(self):
        if not hasattr(self, "_reference_marks"):
            self._reference_marks = FakeNameAccess([])
        return self._reference_marks

    def _sequence_fields(self):
        """Every caption number in the document, in the order they stand in"""
        fields = self.getTextFields()
        return [fields.getByIndex(index)
                for index in range(fields.getCount())
                if hasattr(fields.getByIndex(index), "attachTextFieldMaster")]

    @property
    def LineNumberingProperties(self):
        """How the document numbers its lines, if it does."""
        if not hasattr(self, "_line_numbering"):
            self._line_numbering = FakeLineNumbering()
        return self._line_numbering

    def getTextSections(self):
        """The named regions of the document, by name."""
        if not hasattr(self, "_sections"):
            self._sections = FakeNameAccess([])
            self._text.sections = self._sections.items
        return self._sections

    def getFootnotes(self):
        """The footnotes, by index — UNO offers no names for them."""
        return FakeIndexed([one for one in self._text.notes_in_order()
                            if one.kind == "footnote"])

    def getEndnotes(self):
        return FakeIndexed([one for one in self._text.notes_in_order()
                            if one.kind == "endnote"])

    def getDocumentIndexes(self):
        """The indexes the document holds, by the names they gave themselves"""
        if not hasattr(self, "_document_indexes"):
            self._document_indexes = FakeNameAccess([])
            self._text.indexes = self._document_indexes.items
        return self._document_indexes

    def _index_paragraphs(self):
        """Which body paragraphs belong to an index rather than to the text"""
        taken = set()
        for index in self.getDocumentIndexes().items:
            if index.at is None:
                continue
            taken.update(range(index.at, index.at + len(index.lines)))
        return taken

    def getRedlines(self):
        held = getattr(self, "redlines", None)
        if held is not None:
            return held
        return FakeRedlines(getattr(self, "redline_count", 0))

    def createInstance(self, service):
        if service.startswith("com.sun.star.text.") \
                and service[len("com.sun.star.text."):] in _INDEX_SERVICES:
            return FakeDocumentIndex(
                self, _INDEX_SERVICES[service[len("com.sun.star.text."):]])
        if service == "com.sun.star.text.DocumentIndexMark":
            return FakeIndexMark()
        if service == "com.sun.star.text.TextSection":
            return FakeTextSection(self._text)
        if service == "com.sun.star.text.TextColumns":
            return FakeColumns()
        if service == "com.sun.star.text.Footnote":
            return FakeNote("footnote")
        if service == "com.sun.star.text.Endnote":
            return FakeNote("endnote")
        if service.startswith("com.sun.star.text.TextField.") \
                and service != "com.sun.star.text.TextField.SetExpression" \
                and service != "com.sun.star.text.TextField.GetReference":
            # A field shows something even in a fake, since a header written
            # with {page} in it has to come back with a number in its place.
            kind = service[len("com.sun.star.text.TextField."):]
            shows = {"PageNumber": "1", "PageCount": "1",
                     "DocInfo.Title": "Заголовок",
                     "DocInfo.Subject": "Тема", "Author": "Автор",
                     "FileName": "file.odt",
                     "DateTime": "18.09.2026"}.get(kind, "")
            return FakeField(shows, command=kind, service=kind)
        if service == "com.sun.star.text.TextField.SetExpression":
            field = FakeSequenceField(self, next(self._sequence_ids))
            return field
        if service == "com.sun.star.text.TextField.GetReference":
            return FakeReferenceField(self)
        if service == "com.sun.star.text.FieldMaster.SetExpression":
            return FakeFieldMaster()
        if service == "com.sun.star.text.TextTable":
            return FakeTextTable(f"Table{len(getattr(self, 'tables', [])) + 1}")
        if service == "com.sun.star.text.Bookmark":
            return FakeBookmark()
        if service == "com.sun.star.text.textfield.Annotation":
            note = FakeAnnotation(named=False)
            note.document = self
            return note
        raise RuntimeError(f"no such service in the fake: {service}")

    def getTextFields(self):
        """The annotations the document holds, anchored where their markers are."""
        found = []
        for index in range(len(self._text.paragraphs)):
            offset = 0
            opened = []
            for text, _locale, _props, kind, field in self._text.portions_of(index):
                if kind == "Annotation":
                    opened.append((field, offset))
                elif kind == "AnnotationEnd" and opened:
                    field, start = opened.pop()
                    field._anchor = FakeRange(self._text, (index, start),
                                              (index, offset))
                    found.append(field)
                else:
                    if kind == "TextField" and field is not None:
                        # A field is a text field like a comment, and carries
                        # the characters it shows.
                        field._model = self._text
                        field._paragraph = index
                        field._offset = offset
                        found.append(field)
                    offset += len(text)
            for field, start in opened:
                field._anchor = FakeRange(self._text, (index, start),
                                          (index, start))
                found.append(field)
        return FakeComponents(found)

    @property
    def StyleFamilies(self):
        if not hasattr(self, "_style_families"):
            self._style_families = FakeStyleFamilies()
        return self._style_families

    def supportsService(self, name):
        return name in self.services

    # --- living somewhere: saving, closing, being renamed ---------------
    def hasLocation(self):
        return bool(getattr(self, "url", ""))

    def isModified(self):
        return bool(getattr(self, "modified", False))

    def setModified(self, value):
        self.modified = bool(value)

    def store(self):
        if not self.hasLocation():
            raise RuntimeError("no location to store to")
        if getattr(self, "store_fails", False):
            raise RuntimeError("the disk said no")
        self.stored_in_place = getattr(self, "stored_in_place", 0) + 1
        self.modified = False
        with open(self.url[len("file://"):], "wb") as handle:
            handle.write(b"PK\x03\x04 fake odf")

    def storeAsURL(self, url, arguments):
        settings = {argument.Name: argument.Value for argument in arguments}
        from urllib.parse import unquote, urlparse

        path = unquote(urlparse(url).path)
        self.stored_as = getattr(self, "stored_as", [])
        self.stored_as.append((path, settings.get("FilterName")))
        with open(path, "wb") as handle:
            handle.write(b"PK\x03\x04 fake odf")
        self.url = f"file://{path}"       # the document lives there now
        self.modified = False

    def close(self, deliver_ownership):
        if getattr(self, "close_vetoed", False):
            raise RuntimeError("a listener vetoed the close")
        self.closed = True

    def getURL(self):
        """Where the document lives; "" until it has been saved anywhere."""
        return getattr(self, "url", f"file:///tmp/{self.Title}")


class FakeWriterDoc(FakeDoc):
    services = WRITER_SERVICES
    Title = "fake.odt"

    def __init__(self, text, controller):
        self._text = text
        self._controller = controller
        self.tables = []
        text.owner_document = self          # so a new table joins the document
        self.UndoManager = FakeUndoManager(text)
        # A comment belongs to the document it is in, and follows its
        # "Comment" style unless its own text was typed in a language.
        for paragraph in range(len(text.paragraphs)):
            for _t, _l, _p, _kind, field in text.portions_of(paragraph):
                if isinstance(field, FakeAnnotation):
                    field.document = self
                    if field._own_locale is None:
                        # A note already in a document was marked when it was
                        # created, so it no longer follows the style.
                        field._own_locale = field._style_locale()

    def getText(self):
        return self._text

    def getGraphicObjects(self):
        return FakeNameAccess(getattr(self, "images", ()))

    def getTextTables(self):
        return FakeNameAccess(getattr(self, "tables", ()))

    # --- rendering: what XRenderable and the picture filters answer ---
    pages = 1
    render_failures = ()

    def getRendererCount(self, selection, options):
        return self.pages

    def getRenderer(self, index, selection, options):
        size = FakeSize(21000, 29700)          # A4 in 1/100 mm
        page_size = FakeProperty("PageSize", size)
        return (page_size,)

    def storeToURL(self, url, arguments):
        """Write what the filter would write, so a test can find the file."""
        from urllib.parse import unquote, urlparse

        settings = {argument.Name: argument.Value for argument in arguments}
        filter_name = settings.get("FilterName", "")
        if filter_name in self.render_failures:
            raise RuntimeError(f"{filter_name} refused")
        path = unquote(urlparse(url).path)
        self.stored = getattr(self, "stored", [])
        self.stored.append((filter_name, path, settings))
        with open(path, "wb") as handle:
            handle.write(FakeGraphicProviderPNG if filter_name.endswith(
                "png_Export") else b"%PDF-1.5 fake\n")

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




class FakeModalDialog:
    """What getCurrentComponent() hands back while a dialog has the focus.

    LibreOffice's current component follows the focused frame, so a modal
    dialog or the Start Center answers here instead of the document. It carries
    no Title and supports no document service.
    """

    def supportsService(self, name):
        return False


class FakeDrawDoc:
    """A Draw document holding an imported PDF page, which exports a PNG."""

    def __init__(self):
        self.closed = False
        self.stored = []

    def storeToURL(self, url, arguments):
        from urllib.parse import unquote, urlparse

        settings = {argument.Name: argument.Value for argument in arguments}
        path = unquote(urlparse(url).path)
        self.stored.append((settings.get("FilterName", ""), path, settings))
        with open(path, "wb") as handle:
            handle.write(FakeGraphicProviderPNG)

    def close(self, save):
        self.closed = True


class FakeDesktop:
    def __init__(self, documents, current=None):
        self._documents = list(documents)
        self._current = current if current is not None else (
            documents[0] if documents else None)
        self.loaded = []

    def getCurrentComponent(self):
        return self._current

    def getComponents(self):
        return FakeComponents([document for document in self._documents
                               if not getattr(document, "closed", False)])

    def loadComponentFromURL(self, url, target, flags, arguments):
        """Only the PDF-into-Draw import is modelled, which is all that uses it."""
        settings = {argument.Name: argument.Value for argument in arguments}
        self.loaded.append((url, settings.get("FilterName")))
        drawing = FakeDrawDoc()
        self.drawings = getattr(self, "drawings", [])
        self.drawings.append(drawing)
        return drawing


def writer_doc_with_caret_in_cell(paragraphs, cell_paragraph, caret_offset, page=1):
    """Caret inside a table cell, whose text is separate from the body text."""
    body = FakeText(paragraphs)
    cell = FakeText([cell_paragraph])
    caret = (0, caret_offset)
    view_cursor = FakeViewCursor(cell, caret, caret, page=page)
    selection = FakeSelection(cell, [(caret, caret)])
    return FakeWriterDoc(body, FakeController(view_cursor, selection))


def writer_doc(paragraphs, caret, selection_spans=(), page=1, images=(),
               pages=1, selected_image=None, tables=(), caret_in_cell=None,
               redlines=(), **text_kwargs):
    """Build a Writer document whose caret sits at `caret` = (paragraph, offset).

    `images` describes the pictures in it, each a dict of the arguments
    FakeImage takes besides the model: name, paragraph, offset and whether it
    sits inline in the text.
    """
    text = FakeText(paragraphs, **text_kwargs)
    selection_end = caret
    if selection_spans:
        selection_end = max(max(span) for span in selection_spans)
    view_cursor = FakeViewCursor(text, caret, selection_end, page=page,
                                 pages=pages)
    selection = FakeSelection(text, selection_spans or [(caret, caret)])
    controller = FakeController(view_cursor, selection)
    doc = FakeWriterDoc(text, controller)
    controller.frame.document = doc
    if redlines:
        # Each entry is (paragraph, start, end, kind, author) — the shape of
        # a change Writer recorded and is holding for a reviewer. Left unset
        # otherwise, so a test that only wants a count still gets one.
        doc.redlines = FakeRedlines(entries=[
            FakeRedline(text, *described) for described in redlines])
    doc.images = [FakeImage(model=text, **described) for described in images]
    doc.pages = pages
    doc.tables = list(tables)
    # A table sits between paragraphs in the body, and a range that runs from
    # before it to after it covers it — which is how a selection comes to hold
    # a whole table without its text saying so.
    placed = list(text.enumeration_items)
    for table in doc.tables:
        after = getattr(table, "after_paragraph", None)
        if after is None:
            continue
        table._anchor = FakeRange(text, (after, len(text.paragraphs[after])))
        position = placed.index(after) + 1 if after in placed else len(placed)
        placed.insert(position, table)
    text.enumeration_items = placed
    if caret_in_cell is not None:
        # A cell is its own text, so the caret in one belongs to that text and
        # to no body paragraph — which is why paragraph_index is None there.
        # The view cursor additionally carries the table and the cell.
        table_name, cell_name = caret_in_cell
        table = next(one for one in doc.tables if one.Name == table_name)
        cell = table.getCellByName(cell_name)
        cell_text = FakeText([cell.getString()])
        in_cell = FakeViewCursor(cell_text, (0, 0), (0, 0), page=page,
                                 pages=pages)
        in_cell.TextTable = table
        in_cell.Cell = cell
        doc._controller = FakeController(
            in_cell, FakeSelection(cell_text, [((0, 0), (0, 0))]))
    if selected_image is not None:
        # Selecting a picture in Writer makes the selection the picture
        # itself, with no getCount and no text range in sight.
        picture = next(image for image in doc.images
                       if image.Name == selected_image)
        doc._controller = FakeController(view_cursor, picture)
    return doc
