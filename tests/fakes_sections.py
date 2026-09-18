"""Fakes for text sections: the named regions of a document.

Two measurements are what these carry. A section leaves the paragraph
numbering alone — it is a region of the body text, not a container — and
`IsProtected` protects nothing against the API: Writer stops the reader's
keyboard and lets a tool write straight through. The refusal is the server's,
so the fake has to let the write through the way UNO does, or the test would
prove the guard by accident.
"""


class FakeColumns:
    """com.sun.star.text.TextColumns, which counts 0 until it is given one."""

    def __init__(self, count=0):
        self._count = count

    def getColumnCount(self):
        return self._count

    def setColumnCount(self, count):
        self._count = count


class FakeSectionLink:
    """com.sun.star.text.SectionFileLink: where linked content comes from."""

    def __init__(self):
        self.FileURL = ""
        self.FilterName = ""


class FakeTextSection:
    """com.sun.star.text.TextSection, as the bridge touches one."""

    def __init__(self, model=None):
        self.Name = ""
        self.IsProtected = False
        self.IsVisible = True
        self.Condition = ""
        self.LinkRegion = ""
        self.FileLink = FakeSectionLink()
        self.TextColumns = FakeColumns()
        self._model = model
        self._start = (0, 0)
        self._end = (0, 0)

    @property
    def ParentSection(self):
        """The section this one sits inside, if any.

        Worked out from the ranges, as Writer works it out from the tree.
        """
        if self._model is None:
            return None
        best = None
        for other in getattr(self._model, "sections", []):
            if other is self:
                continue
            if other._start <= self._start and other._end >= self._end:
                if best is None or other._start >= best._start:
                    best = other
        return best

    @property
    def ChildSections(self):
        if self._model is None:
            return ()
        return tuple(other for other in getattr(self._model, "sections", [])
                     if other is not self and other.ParentSection is self)

    def getSupportedServiceNames(self):
        return ("com.sun.star.text.TextContent",
                "com.sun.star.text.TextSection",
                "com.sun.star.document.LinkTarget")

    def supportsService(self, name):
        return name in self.getSupportedServiceNames()

    def getAnchor(self):
        from tests.fakes_text import FakeRange
        return FakeRange(self._model, self._start, self._end)
