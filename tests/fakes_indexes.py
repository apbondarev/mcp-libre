"""Fakes for the indexes a document writes about itself.

What matters about an index, and what these model, is that its entries are
**body paragraphs**: inserting one costs a paragraph and updating it costs as
many as it has entries, so every address below an index moves. Measured on a
real Writer, where a table of contents of three headings went from one
paragraph to four on its first update.

What they do not model is the look of an entry beyond "text, tab, page
number", and page numbers are always 1 — a fake has no pages.
"""

DEFAULT_TITLES = {
    "contents": "Table of Contents",
    "alphabetical": "Alphabetical Index",
    "illustrations": "Table of Figures",
    "tables": "Index of Tables",
    "objects": "Table of Objects",
    "user": "User-Defined",
    "bibliography": "Bibliography",
}

SERVICE_OF_KIND = {
    "contents": "com.sun.star.text.ContentIndex",
    "alphabetical": "com.sun.star.text.DocumentIndex",
    "illustrations": "com.sun.star.text.IllustrationsIndex",
    "tables": "com.sun.star.text.TableIndex",
    "objects": "com.sun.star.text.ObjectIndex",
    # The name an index answers with is not the one it is created under.
    "user": "com.sun.star.text.UserDefinedIndex",
    "bibliography": "com.sun.star.text.Bibliography",
}

NAME_OF_KIND = {kind: DEFAULT_TITLES[kind] for kind in DEFAULT_TITLES}


class FakeIndexMark:
    """com.sun.star.text.DocumentIndexMark: a word listed in the index."""

    def __init__(self):
        self.PrimaryKey = ""
        self.SecondaryKey = ""
        self.AlternativeText = ""
        self._anchor = None

    def getAnchor(self):
        return self._anchor

    def getSupportedServiceNames(self):
        return ("com.sun.star.text.BaseIndexMark",
                "com.sun.star.text.DocumentIndexMark",
                "com.sun.star.text.TextContent")

    def supportsService(self, name):
        return name in self.getSupportedServiceNames()


class FakeDocumentIndex:
    """com.sun.star.text.BaseIndex and its kinds, as the bridge uses one.

    Measured defaults kept here: a content index is created with
    `CreateFromOutline` **False** and `CreateFromMarks` True, so one inserted
    without being told otherwise lists nothing; `IsProtected` is True; and
    the title is the office's own name for that kind.
    """

    def __init__(self, document, kind):
        self.document = document
        self.kind = kind
        self.Title = DEFAULT_TITLES[kind]
        self.Level = 10
        self.CreateFromOutline = False
        self.CreateFromMarks = True
        self.IsProtected = True
        self.Name = ""
        self.at = None
        self.lines = []

    def getSupportedServiceNames(self):
        return ("com.sun.star.text.BaseIndex", SERVICE_OF_KIND[self.kind])

    def supportsService(self, name):
        return name in self.getSupportedServiceNames()

    # -- what it writes into the document -----------------------------

    def _entries(self):
        model = self.document.getText()
        mine = self.document._index_paragraphs()
        if self.kind == "contents" and self.CreateFromOutline:
            return [(f"{model.paragraphs[index]}\t1",
                     f"Contents {min(level, 10)}")
                    for index, level in enumerate(model.outline_levels)
                    if level > 0 and index not in mine and level <= self.Level]
        if self.kind == "alphabetical":
            entries = []
            for mark in getattr(model, "index_marks", []):
                word = mark.AlternativeText or mark.getAnchor().getString()
                entries.append((f"{mark.PrimaryKey}\t", "Index Heading"))
                entries.append((f"{word}\t1", "Index 1"))
            return entries
        return []

    def update(self):
        """Write the index again, moving whatever is below it."""
        model = self.document.getText()
        for _ in range(len(self.lines)):
            model._remove_paragraph_at(self.at)
        # Nothing of this index is in the document while it is being written,
        # which is what keeps its own paragraphs out of what it lists.
        self.lines = []
        written = [(self.Title, "Contents Heading"
                    if self.kind == "contents" else "Index Heading")]
        written.extend(self._entries())
        for offset, (line, style) in enumerate(written):
            model._insert_paragraph_at(self.at + offset, line, style)
        self.lines = [line for line, _style in written]
        return True

    def getAnchor(self):
        from tests.fakes_text import FakeRange
        model = self.document.getText()
        last = self.at + max(0, len(self.lines) - 1)
        return FakeRange(model, (self.at, 0),
                         (last, len(model.paragraphs[last])))
