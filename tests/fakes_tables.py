"""Tables and their cells.

A cell is a text of its own — which is why a caret in one belongs to no body
paragraph — and it answers getText() with itself, which is how the cell that
owns a range is found. Its background is BackColor with BackTransparent off;
it has no FillStyle at all, where a paragraph has the opposite problem.
"""

from tests.fakes_values import (FakeBorderLine, FakeCount, FakeEnumeration,
                                FakeSeparator)
from tests.fakes_text import FakeRange, FakeText


class FakeTableBorder:
    """com.sun.star.table.TableBorder2: the lines, and whether each counts."""

    def __init__(self):
        for field in ("TopLine", "BottomLine", "LeftLine", "RightLine",
                      "HorizontalLine", "VerticalLine"):
            setattr(self, field, FakeBorderLine(18, 0))
        for field in ("IsTopLineValid", "IsBottomLineValid", "IsLeftLineValid",
                      "IsRightLineValid", "IsHorizontalLineValid",
                      "IsVerticalLineValid", "IsDistanceValid"):
            setattr(self, field, False)
        self.Distance = 97


class FakeCellCursor:
    """A cursor over a cell's text, which takes character formatting."""

    def __init__(self, cell):
        self._cell = cell

    def gotoStart(self, expand):
        return True

    def gotoEnd(self, expand):
        return True

    def __setattr__(self, name, value):
        if name.startswith("_"):
            object.__setattr__(self, name, value)
            return
        object.__setattr__(self, name, value)
        self._cell.formatting[name] = value


def _cell_text(text):
    """The text of a cell: its paragraphs, styled as Writer styles them."""
    lines = text.split("\n") if text else [""]
    return FakeText(lines, styles=["Table Contents"] * len(lines))


class FakeCell:
    """A cell is its own XText, which is why a caret in one has no paragraph.

    A cell's background is BackColor with BackTransparent off — it has no
    FillStyle and no FillColor at all, where a paragraph has the opposite
    problem. Measured, and the reason the two are written differently.
    """

    def __init__(self, name, text=""):
        self.CellName = name
        self.BackColor = -1
        self.BackTransparent = True
        self.VertOrient = 0
        self.formatting = {}
        self.model = _cell_text(text)
        self.model.owner = self

    # --- a cell is a text of its own, which is the whole point -----------
    def getString(self):
        return "\n".join(self.model.paragraphs)

    def setString(self, value):
        self.model = _cell_text(value)
        self.model.owner = self

    def createEnumeration(self):
        return self.model.createEnumeration()

    def createTextCursorByRange(self, text_range):
        return self.model.createTextCursorByRange(text_range)

    def getStart(self):
        return FakeRange(self.model, (0, 0))

    def getEnd(self):
        last = len(self.model.paragraphs) - 1
        return FakeRange(self.model, (last, len(self.model.paragraphs[last])))

    def compareRegionStarts(self, first, second):
        """Throws for a range of another cell, which is how they are told apart."""
        return self.model.compareRegionStarts(first, second)

    def supportsService(self, name):
        return name in ("com.sun.star.text.CellProperties",
                        "com.sun.star.text.Text")

    def createTextCursor(self):
        return FakeCellCursor(self)

    def insertTextContent(self, text_range, content, absorb):
        """A comment can be anchored in a cell like anywhere else."""
        return self.model.insertTextContent(text_range, content, absorb)

    def createInstance(self, service):
        return self.model.createInstance(service) \
            if hasattr(self.model, "createInstance") else None

    @property
    def styles(self):
        return list(self.model.styles)


class FakeTextTable:
    """A table in the body enumeration: no getStart(), so the walk must skip it.

    Also a real table when a test gives it cells: named A1, B1, …, with rows
    and columns it can count, and column separators on the 10000 scale that
    Writer measures shares on — its Width is on a scale of its own and is not
    translated, having once invented a table 1.16 metres wide.
    """

    def __init__(self, name="Table1", cells=None, rows=None, columns=None,
                 header_rows=0, merged_away=(), after_paragraph=None):
        self._anchor = None
        self._separators = None
        self._border = FakeTableBorder()
        self.after_paragraph = after_paragraph
        self.Name = name
        self.HeaderRowCount = header_rows
        self.RepeatHeadline = bool(header_rows)
        self.Width = 115596
        self.RelativeWidth = 0
        self.IsWidthRelative = False
        self.TableColumnRelativeSum = 10000
        grid = cells or []
        self._rows = rows if rows is not None else len(grid)
        self._columns = columns if columns is not None else (
            max((len(row) for row in grid), default=0))
        self._cells = {}
        for row_index, row in enumerate(grid):
            for column_index, text in enumerate(row):
                name_of = f"{chr(ord('A') + column_index)}{row_index + 1}"
                if name_of in merged_away:
                    continue
                self._cells[name_of] = FakeCell(name_of, text)

    def getName(self):
        return self.Name

    def supportsService(self, name):
        return name in ("com.sun.star.text.TextTable",
                        "com.sun.star.text.TextContent")

    def getAnchor(self):
        """Where the table sits in the body, as a range of it."""
        if self._anchor is None:
            raise RuntimeError("this table is not in any text")
        return self._anchor

    def getRows(self):
        return FakeCount(self._rows)

    def getColumns(self):
        return FakeCount(self._columns)

    def getCellNames(self):
        return tuple(self._cells)

    def getCellByName(self, name):
        if name not in self._cells:
            raise RuntimeError(f"no cell {name}")
        return self._cells[name]

    @property
    def TableColumnSeparators(self):
        if self._separators is None:
            if self._columns < 2:
                return ()
            share = self.TableColumnRelativeSum // self._columns
            self._separators = [FakeSeparator(share * (index + 1))
                                for index in range(self._columns - 1)]
        return tuple(self._separators)

    @TableColumnSeparators.setter
    def TableColumnSeparators(self, separators):
        self._separators = list(separators)

    @property
    def TableBorder2(self):
        """The grid, as a struct whose Is*Valid flags decide what sticks."""
        return self._border

    @TableBorder2.setter
    def TableBorder2(self, border):
        self._border = border
