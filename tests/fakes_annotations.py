"""Comments and pictures: the things anchored to text rather than made of it.

A comment is empty marker portions carrying a field; a picture is an empty
portion of type Frame. Neither costs a character, which is how reading runs
came to skip both.
"""

import itertools

from tests.fakes_values import FakeDateTime, FakeEnum, FakeLocale, FakeSize
from tests.fakes_text import FakeRange


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
        return self._anchor

    @property
    def TextRange(self):
        return FakeNoteText(self)

    def dispose(self):
        self.disposed = True
