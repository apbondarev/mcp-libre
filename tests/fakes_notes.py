"""Fakes for footnotes and endnotes.

The measurement that matters: a note's mark is a **character of the
paragraph**, not an empty marker — "A query" with a footnote after "query"
reads `A query1 is the entry point` — and the portion carrying it is of type
`Footnote`. So a note behaves here the way a field does, and rewriting the
run its mark sits in destroys it.

An endnote supports `com.sun.star.text.Footnote` as well as
`com.sun.star.text.Endnote`, measured on a real Writer, so anything telling
them apart has to ask for Endnote.
"""


class FakeNoteBody:
    """The note's own text: an XText of its own, at the foot of the page."""

    def __init__(self):
        self._text = ""

    def getString(self):
        return self._text

    def setString(self, value):
        self._text = value


class FakeNote:
    """com.sun.star.text.Footnote, and Endnote, as the bridge touches one."""

    def __init__(self, kind="footnote"):
        self.kind = kind
        self.Label = ""
        self.ReferenceId = 0
        self._body = FakeNoteBody()
        self._model = None
        self._paragraph = 0
        self._offset = 0

    def getText(self):
        return self._body

    def getSupportedServiceNames(self):
        names = ["com.sun.star.text.TextContent",
                 "com.sun.star.text.Footnote", "com.sun.star.text.Text"]
        if self.kind == "endnote":
            names.append("com.sun.star.text.Endnote")
        return tuple(names)

    def supportsService(self, name):
        return name in self.getSupportedServiceNames()

    def getAnchor(self):
        from tests.fakes_text import FakeRange
        return FakeRange(self._model, (self._paragraph, self._offset),
                         (self._paragraph, self._offset + 1))
