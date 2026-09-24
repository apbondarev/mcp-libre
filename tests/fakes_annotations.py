"""Comments and pictures: the things anchored to text rather than made of it.

A comment is empty marker portions carrying a field; a picture is an empty
portion of type Frame. Neither costs a character, which is how reading runs
came to skip both.
"""

import itertools

from tests.fakes_values import (FakeDateTime, FakeEnum, FakeEnumeration,
                                FakeLocale, FakeSize)
from tests.fakes_text import FakeRange, FakeTextPortion


class FakeGraphic:
    """The XGraphic behind a picture: what it is, not where it sits."""

    def __init__(self, mime_type="image/png", pixels=(8, 8), linked=False,
                 origin=""):
        self.MimeType = mime_type
        self.SizePixel = FakeSize(*pixels)
        self.Linked = linked
        self.OriginURL = origin


class FakeImage:
    """com.sun.star.text.TextGraphicObject, as the bridge touches it.

    A picture adds no characters to the paragraph it sits in and shows up in
    the portions as an empty one of type "Frame" — which is how reading runs
    came to skip it and report nothing at all.
    """

    def __init__(self, name, model, paragraph, offset, inline=True, title="",
                 description="", width=800, height=600, graphic=None):
        self.Name = name
        self.Title = title
        self.Description = description
        self.AnchorType = FakeEnum("AS_CHARACTER" if inline else "AT_CHARACTER")
        self.Width = width
        self.Height = height
        self.Graphic = graphic if graphic is not None else FakeGraphic()
        self.AnchorPageNo = 0
        self._model = model
        self._anchor = FakeRange(model, (paragraph, offset))

    def getAnchor(self):
        return self._anchor

    def getName(self):
        return self.Name

    def supportsService(self, name):
        return name in ("com.sun.star.text.TextGraphicObject",
                        "com.sun.star.text.TextContent",
                        "com.sun.star.text.BaseFrame")


class FakeBookmark:
    """com.sun.star.text.Bookmark: a name the document keeps for a place.

    Measured on a real Writer: it moves with its text, survives a rewrite of
    the very words it covers — where a comment or a field would go — and a
    name that is taken is *not* refused: Writer mints "name Copy 1".
    """

    def __init__(self, name=""):
        self._name = name
        self._anchor = None

    def getName(self):
        return self._name

    def setName(self, name):
        self._name = name

    def getAnchor(self):
        if self._anchor is None:
            raise RuntimeError("this bookmark is in no text")
        return self._anchor

    def supportsService(self, name):
        return name in ("com.sun.star.text.Bookmark",
                        "com.sun.star.text.TextContent")


class FakeMathModel:
    """The Math document inside a formula: `Formula` is all the bridge uses."""

    def __init__(self, formula=""):
        self.Formula = formula


class FakeVisualArea:
    """What the Math document says it takes: it answers in twips (9), and a
    longer formula is a wider one — measured, a fraction was 172 x 569 twips
    and "a = b" 462 x 267."""

    def __init__(self, model):
        self._model = model

    def getMapUnit(self, aspect):
        return 9

    def getVisualAreaSize(self, aspect):
        from tests.fakes_values import FakeSize
        text = getattr(self._model, "Formula", "")
        return FakeSize(100 * len(text), 300)


class FakeChartModel:
    """An embedded object that is not a formula: there is no Formula on it."""


class FakeFormulaObject:
    """com.sun.star.text.TextEmbeddedObject, as the bridge touches it.

    Measured on a real Writer: an inline one adds no character to its
    paragraph and is anchored at an empty range; Writer names it itself when
    it is put in; and a name that is taken is refused with an error, where a
    bookmark would be quietly renamed.
    """

    MATH_CLSID = "078B7ABA-54FC-457F-8551-6147e776a997"

    def __init__(self, formula="", name="", chart=False):
        # What tells a formula from a chart without loading the object:
        # Writer stamps the Math class id on it, and asking for `Model`
        # instead costs the load.
        self.CLSID = "" if chart else self.MATH_CLSID
        self.AnchorType = None
        self.Width = 3528
        self.Height = 471
        self.Model = FakeChartModel() if chart else FakeMathModel(formula)
        self._name = name
        self._anchor = None
        self._siblings = []

    def getEmbeddedObject(self):
        return FakeVisualArea(self.Model)

    def getName(self):
        return self._name

    def setName(self, name):
        if any(other is not self and other.getName() == name
               for other in self._siblings):
            raise RuntimeError("SwXFrame::setName(): Illegal object name. "
                               "Duplicate name?")
        self._name = name

    def getAnchor(self):
        if self._anchor is None:
            raise RuntimeError("this object is in no text")
        return self._anchor


class FakeField:
    """com.sun.star.text.TextField, as the bridge touches it.

    Unlike a comment's marker a field *carries* the text it shows, so the
    paragraph's string is longer than a walk over its Text portions — which
    is what put every address after a date seven characters to the right.
    """

    def __init__(self, shows, command="Date", service="DocInfo.Title",
                 model=None, paragraph=0, offset=0, fixed=False):
        self._shows = shows
        self._command = command
        self.IsFixed = fixed
        self.IsDate = command == "Date"
        self._service = f"com.sun.star.text.TextField.{service}"
        self._model = model
        self._paragraph = paragraph
        self._offset = offset

    def getPresentation(self, command):
        return self._command if command else self._shows

    def getSupportedServiceNames(self):
        return (self._service, "com.sun.star.text.TextField")

    def supportsService(self, name):
        return name in self.getSupportedServiceNames()

    def getAnchor(self):
        return FakeRange(self._model, (self._paragraph, self._offset),
                         (self._paragraph, self._offset + len(self._shows)))


_annotation_serial = itertools.count(1)


class FakeNoteCursor:
    """A cursor over a comment's own text, which is all the bridge needs."""

    def __init__(self, note):
        self._note = note

    def gotoStart(self, expand):
        return True

    def gotoEnd(self, expand):
        return True

    @property
    def CharLocale(self):
        return self._note.locale

    @CharLocale.setter
    def CharLocale(self, value):
        self._note.locale = value


class FakeNoteParagraph:
    def __init__(self, note):
        self._note = note

    def createEnumeration(self):
        return FakeEnumeration([FakeTextPortion(self._note.Content,
                                                self._note.locale)])


class FakeNoteText:
    """The annotation's TextRange: the note in the margin, spell checked."""

    def __init__(self, note):
        self._note = note

    def createEnumeration(self):
        return FakeEnumeration([FakeNoteParagraph(self._note)])

    def createTextCursor(self):
        return FakeNoteCursor(self._note)

    @property
    def CharLocale(self):
        return self._note.locale


class FakeAnnotation:
    """com.sun.star.text.textfield.Annotation, as the bridge touches it.

    Writer mints a unique Name per comment ("__Annotation__16442_3809372040"),
    which is the only stable way to name one for editing or deleting: two
    comments can share an author, a text and an anchor.
    """

    def __init__(self, author="", content="", resolved=False, initials="",
                 parent="", named=True, language=None):
        self.Author = author
        self._content = content
        self.Resolved = resolved
        self.Initials = initials
        self.ParentName = parent
        # Writer names the comments made in its own interface; one created
        # through the API comes back with an empty Name, so createInstance
        # hands out an unnamed one and whoever inserts it must name it.
        self.Name = (f"__Annotation__{next(_annotation_serial)}_fake"
                     if named else "")
        self.DateTimeValue = (FakeDateTime() if named
                              else FakeDateTime(year=0, month=0, day=0, hours=0,
                                                minutes=0, seconds=0))
        # A comment typed in Writer carries its own language; one created
        # through the API has none and follows the "Comment" paragraph style,
        # which is the only place a language can be written at all.
        self._own_locale = FakeLocale(*language.split("-")) if language else None
        self.document = None

    @property
    def Content(self):
        return self._content

    @Content.setter
    def Content(self, value):
        """Writing the text does not change the note's language.

        A note is marked with the "Comment" style's language when it is
        created, and writing its text again afterwards leaves that language
        alone — measured on a live LibreOffice, after a fake that restamped
        on every write had made an unusable feature look like it worked.
        """
        if not value or value == self._content:
            return
        self._content = value
        if self._own_locale is None:
            self._own_locale = self._style_locale()

    def _style_locale(self):
        if self.document is None:
            return FakeLocale("en", "US")
        return self.document.StyleFamilies.getByName(
            "ParagraphStyles").getByName("Comment").CharLocale

    @property
    def locale(self):
        if self._own_locale is not None:
            return self._own_locale
        return self._style_locale()

    @locale.setter
    def locale(self, value):
        self._own_locale = value
        self._anchor = None
        self.disposed = False

    def supportsService(self, name):
        return name == "com.sun.star.text.textfield.Annotation"

    def getAnchor(self):
        # The document holds a comment's anchor, so a real one answers
        # whoever asks — through the document's text fields or through the
        # portions of the paragraph it marks. The fake used to be given its
        # anchor only by getTextFields(), so a note reached the other way had
        # none, and a scoped listing found nothing.
        model = getattr(self, "_model", None)
        if model is not None:
            span = model.comment_span(self)
            if span is not None:
                return FakeRange(model, span[0], span[1])
        return self._anchor

    @property
    def TextRange(self):
        return FakeNoteText(self)

    def dispose(self):
        self.disposed = True
