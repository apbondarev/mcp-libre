"""Fakes for captions and cross-references.

What is modelled is the bookkeeping the bridge does: which sequence a
caption's number counts in, what a reference points at, and what it shows
when the thing it points at is there or gone. The *numbers themselves* are
worked out from the order the fields stand in, which is what a real Writer
does on `refresh()`.

Two things these fakes deliberately do not model, because the live harness
proves them instead: the characters a field contributes to the paragraph
string are written once, when the field goes in, so a renumbering does not
rewrite the line behind it; and Writer's own handling of a bookmark named
`__RefHeading__…`, which turns it into a cross-reference mark that
`getBookmarks()` never shows.
"""

ROMAN = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"),
         (90, "XC"), (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"),
         (4, "IV"), (1, "I")]


def _roman(number):
    out = []
    for value, letters in ROMAN:
        while number >= value:
            out.append(letters)
            number -= value
    return "".join(out)


def _numbered(value, numbering_type):
    """com.sun.star.style.NumberingType, the few kinds a caption uses."""
    if numbering_type == 2:
        return _roman(value)
    if numbering_type == 3:
        return _roman(value).lower()
    if numbering_type == 0:
        return chr(ord("A") + (value - 1) % 26)
    if numbering_type == 1:
        return chr(ord("a") + (value - 1) % 26)
    return str(value)


class FakeFieldMaster:
    """com.sun.star.text.FieldMaster.SetExpression — a sequence by name."""

    def __init__(self, name="", sub_type=1):
        self.Name = name
        self.SubType = sub_type

    @property
    def InstanceName(self):
        return "com.sun.star.text.fieldmaster.SetExpression." + self.Name


class FakeSequenceField:
    """The number in a caption: a field that counts itself.

    Measured on a real Writer and kept here: `SequenceValue` is an identity
    and not a number — captions made 1, 2, 3 and then one put in front of
    them read 2, 0, 1, 3 while showing 2, 3, 4, 1 — so what the field shows
    is its place among the captions of its category, in document order.
    """

    def __init__(self, document=None, sequence_id=0):
        self.document = document
        self.Content = ""
        self.NumberingType = 4
        self.SequenceValue = sequence_id
        self._master = None
        self._model = None
        self._paragraph = 0
        self._offset = 0

    def attachTextFieldMaster(self, master):
        """A master nobody had is registered by the field that attaches to it.

        That is what a real office does: a sequence exists once something
        counts in it, which is why a category of one's own needs no setting up.
        """
        self._master = master
        if self.document is not None and master is not None:
            self.document.getTextFieldMasters()
            self.document._masters.setdefault(master.Name, master)
        return True

    @property
    def TextFieldMaster(self):
        return self._master

    @property
    def category(self):
        return self._master.Name if self._master else None

    def getPresentation(self, command):
        if command:
            return f"Number range {self.category}"
        return _numbered(self._place(), self.NumberingType)

    def _place(self):
        """Which caption of this category this one is, counting from one."""
        if self.document is None:
            return 1
        siblings = [field for field in self.document._sequence_fields()
                    if getattr(field, "category", None) == self.category]
        for position, field in enumerate(siblings, start=1):
            if field is self:
                return position
        return len(siblings) + 1

    def getSupportedServiceNames(self):
        return ("com.sun.star.text.TextField.SetExpression",
                "com.sun.star.text.TextField")

    def supportsService(self, name):
        return name in self.getSupportedServiceNames()

    def getAnchor(self):
        from tests.fakes_text import FakeRange
        return FakeRange(self._model, (self._paragraph, self._offset),
                         (self._paragraph, self._offset))


class FakeReferenceField:
    """com.sun.star.text.TextField.GetReference: a pointer at something else.

    What it shows is worked out when it is asked, from whatever the document
    holds now — which is how a reference follows its target. A target that is
    gone gives Writer's own sentence, in English here and in the office's
    language in a real document, which is exactly why the tools check the
    target instead of reading this.
    """

    NOT_FOUND = "Error: Reference source not found"

    def __init__(self, document=None):
        self.document = document
        self.ReferenceFieldSource = 0
        self.SourceName = ""
        self.SequenceNumber = 0
        self.ReferenceFieldPart = 2
        self._model = None
        self._paragraph = 0
        self._offset = 0

    def getSupportedServiceNames(self):
        return ("com.sun.star.text.TextField.GetReference",
                "com.sun.star.text.TextField")

    def supportsService(self, name):
        return name in self.getSupportedServiceNames()

    def getAnchor(self):
        from tests.fakes_text import FakeRange
        return FakeRange(self._model, (self._paragraph, self._offset),
                         (self._paragraph, self._offset))

    def getPresentation(self, command):
        if command:
            return " " + self.SourceName
        if self.ReferenceFieldSource == 1:
            return self._from_caption()
        return self._from_bookmark()

    def _from_caption(self):
        caption = next(
            (field for field in self.document._sequence_fields()
             if getattr(field, "category", None) == self.SourceName
             and field.SequenceValue == self.SequenceNumber), None)
        if caption is None:
            return self.NOT_FOUND
        number = caption.getPresentation(False)
        if self.ReferenceFieldPart == 5:
            return f"{self.SourceName} {number}"
        if self.ReferenceFieldPart == 7:
            return number
        line = self.document._text.paragraphs[caption._paragraph]
        after = line.split(number, 1)[-1]
        return after[2:] if after.startswith(": ") else after

    def _from_bookmark(self):
        marks = self.document.getBookmarks()
        if not marks.hasByName(self.SourceName):
            return self.NOT_FOUND
        return marks.getByName(self.SourceName).getAnchor().getString()
