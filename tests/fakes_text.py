"""The text model behind every fake document.

A range belongs to the text that owns it and a foreign one is refused, the
way Writer refuses it; a paragraph's portions carry their own language and
formatting; and the markers a comment or a picture leaves behind cost no
characters. This is where the fakes are faithful or useless.
"""

from tests.fakes_values import (FakeEnum, FakeEnumeration, FakeLocale,
                                FakeSize)


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
        # A range inside a cell answers with the cell, not with the text
        # behind it — which is how the cell owning a range is found.
        return getattr(self.model, "owner", None) or self.model

    def setString(self, value):
        self.model.replace_range(self.start, self.end, value)

    def getStart(self):
        return FakeRange(self.model, self.start)

    def getEnd(self):
        return FakeRange(self.model, self.end)

    def isCollapsed(self):
        return self.start == self.end


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
        # A range inside a cell answers with the cell, not with the text
        # behind it — which is how the cell owning a range is found.
        return getattr(self.model, "owner", None) or self.model

    def setString(self, value):
        """Rewrite the span.

        Measured: the cursor that does this keeps the new text, while any
        other cursor over the same stretch collapses to an empty position.
        """
        low, _ = sorted([self.start, self.end])
        self.model.replace_range(self.start, self.end, value, writer=self)
        self.mark = low
        self.pos = low if "\n" in value else (low[0], low[1] + len(value))

    def getStart(self):
        return FakeRange(self.model, self.start)

    def getEnd(self):
        return FakeRange(self.model, self.end)

    def isCollapsed(self):
        return self.start == self.end

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

    def gotoRange(self, other, expand):
        """Send the cursor to another range in the same text."""
        if getattr(other, "model", None) is not self.model:
            raise RuntimeError(
                "End of content node doesn't have the proper start node")
        self.pos = other.end if hasattr(other, "end") else other.start
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
    """com.sun.star.text.Paragraph — the paragraph itself, not a number.

    Measured on a real Writer, and kept here: a held paragraph object
    follows its paragraph when paragraphs are inserted above it, stays the
    same paragraph when its whole text is rewritten through another cursor,
    goes with the text *after* the cut when the paragraph is split, and
    **throws** ("SwXParagraph: disposed or invalid") once the paragraph is
    merged into the one before it or removed. A fake that pinned the number
    at creation would have let a batch pin its paragraphs and prove nothing.
    """

    def __init__(self, model, index):
        ids = getattr(model, "paragraph_ids", None)
        self.pid = ids[index] if ids is not None and index < len(ids) else None
        self._fixed = index
        super().__init__(model, (index, 0), (index, len(model.paragraphs[index])))
        if model.expose_outline_level:
            self.OutlineLevel = model.outline_levels[index]

    @property
    def index(self):
        if self.pid is None:
            return self._fixed
        try:
            return self.model.paragraph_ids.index(self.pid)
        except ValueError:
            raise RuntimeError("SwXParagraph: disposed or invalid at "
                               "./sw/source/core/unocore/unoparagraph.cxx")

    @property
    def start(self):
        return (self.index, 0)

    @start.setter
    def start(self, value):
        pass                      # where it starts follows the paragraph

    @property
    def end(self):
        index = self.index
        return (index, len(self.model.paragraphs[index]))

    @end.setter
    def end(self, value):
        pass

    # -- page breaks, which belong to the paragraph after them --------
    #
    # Measured on a live Writer: BreakType is an enum whose .value reads
    # "PAGE_BEFORE"; PageDescName refuses None with a CannotConvertException
    # and takes "" instead, reading back as None; and PageNumberOffset
    # refuses None the same way.

    def getPropertyState(self, name):
        from tests.fakes_values import FakeEnum
        if name.startswith("Char"):
            # Character formatting sits on the paragraph when it was applied
            # to the whole of it — which is the case a clean-up must not miss.
            return FakeEnum(self.model.paragraph_state(self.index, name))
        return FakeEnum(self.model.paragraph_state(self.index, name))

    def getPropertyStates(self, names):
        from tests.fakes_values import FakeEnum
        return tuple(FakeEnum(self.getPropertyState(name).value)
                     for name in names)

    def setPropertyToDefault(self, name):
        self.model.clear_paragraph_property(self.index, name)

    @property
    def BreakType(self):
        from tests.fakes_values import FakeEnum
        return FakeEnum(self.model.breaks.get(self.index, {})
                        .get("BreakType", "NONE"))

    @BreakType.setter
    def BreakType(self, value):
        name = getattr(value, "value", value)
        self.model.breaks.setdefault(self.index, {})["BreakType"] = name

    @property
    def PageDescName(self):
        return self.model.breaks.get(self.index, {}).get("PageDescName")

    @PageDescName.setter
    def PageDescName(self, value):
        if value is None:
            raise RuntimeError("Type 0 is not supported! at "
                               "./stoc/source/typeconv/convert.cxx:444")
        self.model.breaks.setdefault(self.index, {})["PageDescName"] = \
            value or None

    @property
    def PageNumberOffset(self):
        return self.model.breaks.get(self.index, {}).get("PageNumberOffset")

    @PageNumberOffset.setter
    def PageNumberOffset(self, value):
        if value is None:
            raise RuntimeError("Type 0 is not supported! at "
                               "./stoc/source/typeconv/convert.cxx:364")
        self.model.breaks.setdefault(self.index, {})["PageNumberOffset"] = value

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
        portions = []
        offset = 0
        for text, locale, properties, kind, field in self.model.portions_of(
                self.index):
            portions.append(FakeTextPortion(text, locale, properties, kind,
                                            field, self.model, self.index,
                                            offset))
            offset += len(text)
        return FakeEnumeration(portions)


class FakeTextPortion:
    """A run inside a paragraph, carrying its own language and formatting.

    A comment is not text: Writer represents one as empty marker portions —
    Annotation ... AnnotationEnd around the commented range, or a lone
    Annotation for a comment anchored to a point. TextPortionType tells them
    apart, and skipping them as "empty runs" is how a rewrite destroys them.
    """

    DEFAULTS = {"CharWeight": 100.0, "CharPosture": "NONE", "CharUnderline": 0,
                "CharHeight": 12.0, "CharFontName": "Liberation Serif",
                "CharColor": -1, "CharBackColor": -1, "HyperLinkURL": "",
                "HyperLinkTarget": "", "CharStyleName": ""}

    def __init__(self, text, locale, properties=None, kind="Text", field=None,
                 model=None, paragraph=0, offset=0):
        self._text = text
        self.CharLocale = locale
        self.TextPortionType = kind
        self.TextField = field
        self.model = model
        self.paragraph = paragraph
        self.offset = offset
        for name, value in dict(self.DEFAULTS, **(properties or {})).items():
            setattr(self, name, value)

    def getString(self):
        return self._text

    def getStart(self):
        return FakeRange(self.model, (self.paragraph, self.offset))

    def getEnd(self):
        return FakeRange(self.model,
                         (self.paragraph, self.offset + len(self._text)))


def _property_states(self, names):
    """The states of several properties in one call, as UNO answers them."""
    from tests.fakes_values import FakeEnum
    return tuple(FakeEnum(self.getPropertyState(name).value)
                 for name in names)


def _range_property_state(self, name):
    from tests.fakes_values import FakeEnum
    return FakeEnum(self.model.char_state(self.start, self.end, name))


def _range_property_to_default(self, name):
    self.model.clear_char_property(self.start, self.end, name)


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


def _insert_text_content(self, text_range, content, absorb):
    """Anchoring an annotation to a range, as insertTextContent does."""
    self.model.insert_comment(text_range.start, text_range.end, content)


class FakeText:
    """Models com.sun.star.text.Text: cursor factory, enumeration, comparison."""

    def _insert_paragraph_at(self, index, line, style="Standard", level=0):
        """Put a paragraph in, moving everything below it down one.

        The enumeration keeps its order — a table sits *between* paragraphs,
        and sorting the items would have moved every table to the end.
        """
        self._move_cursors(lambda paragraph, offset:
                           (paragraph + 1, offset) if paragraph >= index
                           else (paragraph, offset))
        self.paragraphs.insert(index, line)
        self.paragraph_ids.insert(index, self._next_paragraph_id)
        self._next_paragraph_id += 1
        self.styles.insert(index, style)
        self.outline_levels.insert(index, level)
        self.portions = {(key + 1 if key >= index else key): value
                         for key, value in self.portions.items()}
        items, placed = [], False
        for item in self.enumeration_items:
            if isinstance(item, int):
                if item >= index and not placed:
                    items.append(index)
                    placed = True
                items.append(item + 1 if item >= index else item)
            else:
                items.append(item)
        if not placed:
            items.append(index)
        self.enumeration_items = items

    def take_paragraph(self, index):
        """Lift a paragraph out whole — its text, style and portions.

        A move is not a rewrite: what hangs off the paragraph travels with
        it, which is exactly why Writer's own command is the way to move one.
        """
        piece = {"text": self.paragraphs[index],
                 "style": self.styles[index],
                 "level": self.outline_levels[index],
                 "portions": self.portions.get(index),
                 "id": self.paragraph_ids[index]}
        self._remove_paragraph_at(index)
        return piece

    def put_paragraph(self, index, piece):
        """Put a lifted paragraph back in, with everything it carried."""
        self._insert_paragraph_at(index, piece["text"], style=piece["style"],
                                  level=piece["level"])
        if piece["portions"] is not None:
            self.portions[index] = piece["portions"]
        if piece.get("id") is not None:
            self.paragraph_ids[index] = piece["id"]

    def _remove_paragraph_at(self, index):
        """Take a paragraph out, moving everything below it up one."""
        self._paragraph_removed(index)
        del self.paragraphs[index]
        del self.paragraph_ids[index]
        del self.styles[index]
        del self.outline_levels[index]
        self.portions.pop(index, None)
        self.portions = {(key - 1 if key > index else key): value
                         for key, value in self.portions.items()}
        self.enumeration_items = [
            item if not isinstance(item, int) else
            (item - 1 if item > index else item)
            for item in self.enumeration_items
            if not isinstance(item, int) or item != index]

    def insertControlCharacter(self, text_range, character, absorb):
        """A paragraph break splits the paragraph where the range starts.

        Measured on a real Writer and kept here: a break at the **end** of a
        paragraph leaves an empty paragraph directly after it, and one at the
        **start** leaves an empty one directly before, which is how a caption
        paragraph is made. A heading split this way does not go on being a
        heading, since Writer follows it with its FollowStyle.
        """
        index, offset = text_range.start
        line = self.paragraphs[index]
        head, tail = line[:offset], line[offset:]
        heading = self.outline_levels[index] > 0
        declared = self.portions.pop(index, None)
        self.paragraphs[index] = head
        self._insert_paragraph_at(
            index + 1, tail,
            style="Standard" if heading else self.styles[index],
            level=0 if heading else self.outline_levels[index])
        # Measured: a held paragraph object goes with the text *after* the
        # cut — "P4 four" split after "P4" left it on " four" — so the new
        # paragraph is the one before.
        ids = self.paragraph_ids
        ids[index], ids[index + 1] = ids[index + 1], ids[index]
        # And a cursor at or after the cut goes with that text too — a point
        # exactly at the cut slides past it, the way a point slides past text
        # inserted exactly where it stands (measured: offset 3 became 6).
        self._move_cursors(lambda paragraph, where:
                           (index + 1, where - offset)
                           if paragraph == index and where >= offset
                           else (paragraph, where))
        if declared is not None:
            before, after = self._split_portions(declared, offset)
            self.portions[index] = before
            self.portions[index + 1] = after
        # The cursor that made the break is left in the new paragraph, as a
        # real one is.
        if hasattr(text_range, "mark"):
            text_range.mark = text_range.pos = (index + 1, 0)
        else:
            text_range.start = text_range.end = (index + 1, 0)

    def convertToTable(self, ranges, cell_properties, row_properties,
                       table_properties):
        """XTextConvert.convertToTable: rows, cells, and two ranges each.

        The signature was read off a running office by introspection —
        `[][][]XTextRange`, not properties — and the measured behaviour it
        carries is that **nothing between the first cell's start and the last
        cell's end is dropped**: a separator left between two cell ranges
        turns up at the head of the next cell. The tool takes the separators
        out first, and so the fake expects cells that already sit end to end.
        """
        from tests.fakes_tables import FakeTextTable

        rows = [[(one[0], one[1]) for one in row] for row in ranges]
        if not rows:
            return None
        first = rows[0][0][0].start[0]
        last = rows[-1][-1][1].start[0]
        table = FakeTextTable(f"Table{len(getattr(self.owner_document, 'tables', [])) + 1}")
        table.initialize(len(rows), len(rows[0]))
        for number, row in enumerate(rows, start=1):
            for column, (start, end) in enumerate(row):
                name = f"{chr(ord('A') + column)}{number}"
                table.getCellByName(name).setString(
                    self.slice_text(start.start, end.end))
        # The paragraphs go, and the table stands where they were.
        for _ in range(last - first + 1):
            self._remove_paragraph_at(first)
        items = list(self.enumeration_items)
        position = 0
        for index, item in enumerate(items):
            if isinstance(item, int) and item >= first:
                position = index
                break
            position = index + 1
        items.insert(position, table)
        self.enumeration_items = items
        if self.owner_document is not None:
            self.owner_document.tables.append(table)
        table._anchor = FakeRange(self, (min(first, max(0, len(self.paragraphs) - 1)), 0))
        table.after_paragraph = max(0, first - 1)
        return table

    def insert_index(self, text_range, index):
        """An index lands between paragraphs, before the one it is put at.

        Measured: it costs **one** paragraph going in, and as many as it has
        entries on its first update — which is why every address below an
        index moves when the index is written.
        """
        at = text_range.start[0]
        self._insert_paragraph_at(at, "", style="Contents Heading")
        index.at = at
        index.lines = [""]
        document = self.owner_document
        if document is not None:
            held = document.getDocumentIndexes().items
            same = sum(1 for one in held if one.kind == index.kind)
            from tests.fakes_indexes import NAME_OF_KIND
            index.Name = f"{NAME_OF_KIND[index.kind]}{same + 1}"
            held.append(index)
            for other in held:
                if other is not index and other.at is not None \
                        and other.at >= at:
                    other.at += 1

    def remove_index(self, index):
        """Taking an index away takes the paragraphs it wrote with it."""
        for _ in range(len(index.lines)):
            self._remove_paragraph_at(index.at)
        document = self.owner_document
        if document is not None:
            held = document.getDocumentIndexes().items
            if index in held:
                held.remove(index)
            for other in held:
                if other.at is not None and other.at > index.at:
                    other.at -= len(index.lines)
        index.at = None
        index.lines = []

    def _split_portions(self, declared, offset):
        """The portions of a paragraph, cut in two at a character offset"""
        before, after, seen = [], [], 0
        for piece in declared:
            piece = dict(piece) if isinstance(piece, dict) else \
                {"text": piece[0], "locale": piece[1]}
            text = piece.get("text", "")
            if seen >= offset:
                after.append(piece)
            elif seen + len(text) <= offset:
                before.append(piece)
            else:
                cut = offset - seen
                before.append(dict(piece, text=text[:cut]))
                after.append(dict(piece, text=text[cut:]))
            seen += len(text)
        return before, after

    def _note_mark(self, note):
        """What the mark reads: the label, or Writer's own numbering."""
        if note.Label:
            return note.Label
        same = [one for one in self.notes_in_order() if one.kind == note.kind]
        return str(len(same) + 1)

    def notes_in_order(self):
        """Every footnote and endnote in the text, in the order they sit in"""
        found = []
        for index in range(len(self.paragraphs)):
            for piece in self.portions.get(index, []):
                if isinstance(piece, dict) and piece.get("kind") == "Footnote" \
                        and piece.get("Footnote") is not None:
                    found.append(piece["Footnote"])
        return found

    def insert_field(self, where, field, shown, kind="TextField",
                     key="field"):
        """A field goes in carrying the characters it shows.

        The mirror of a comment, measured on a real Writer: the portion is of
        type TextField and its string is part of the paragraph, so every
        offset after it moves.
        """
        position = where if isinstance(where, tuple) else where.start
        index, offset = position
        line = self.paragraphs[index]
        declared = self.portions.get(index)
        if declared is None:
            declared = [{"text": line}] if line else []
        before, after = self._split_portions(declared, offset)
        self.portions[index] = before + [{"text": shown, "kind": kind,
                                          key: field}] + after
        self.paragraphs[index] = line[:offset] + shown + line[offset:]
        field._model = self
        field._paragraph = index
        field._offset = offset
        if hasattr(where, "mark"):
            where.mark = where.pos = (index, offset + len(shown))

    def insertTextContent(self, text_range, content, absorb):
        """A bookmark goes on the range and changes no text."""
        if hasattr(content, "IsProtected") and hasattr(content, "IsVisible"):
            # A section covers the range it is put on and moves nothing: the
            # paragraph numbering is exactly what it was — measured.
            content._model = self
            content._start = text_range.start
            content._end = text_range.end
            if self.owner_document is not None:
                # This hands `self.sections` its list, so the section is
                # appended once and both sides see it.
                self.owner_document.getTextSections()
            if not hasattr(self, "sections"):
                self.sections = []
            self.sections.append(content)
            return
        if hasattr(content, "update") and hasattr(content, "IsProtected"):
            self.insert_index(text_range, content)
            return
        if hasattr(content, "PrimaryKey"):
            # An index mark covers its text and leaves it, like a bookmark.
            content._anchor = FakeRange(self, text_range.start, text_range.end)
            if not hasattr(self, "index_marks"):
                self.index_marks = []
            self.index_marks.append(content)
            return
        if hasattr(content, "getText") and hasattr(content, "Label") \
                and hasattr(content, "kind"):
            # A note's mark is one character of the paragraph, and the
            # portion carrying it is of type Footnote — the same shape as a
            # field, which is why it goes in the same way.
            self.insert_field(text_range, content, self._note_mark(content),
                              kind="Footnote", key="Footnote")
            return
        if hasattr(content, "getPresentation") \
                and not hasattr(content, "Author"):
            # A caption's number or a cross-reference: a field, not a note.
            self.insert_field(text_range, content,
                              content.getPresentation(False))
            return
        if hasattr(content, "getName") and hasattr(content, "setName") \
                and not hasattr(content, "getCellNames"):
            content._anchor = FakeRange(self, text_range.start, text_range.end)
            if hasattr(self, "bookmarks"):
                self.bookmarks.append(content)
            return
        return self._insert_text_content(text_range, content, absorb)

    def _insert_text_content(self, text_range, content, absorb):
        """A comment is anchored to a range; a table takes its place between
        paragraphs, before the one the range starts in — which is where a
        real one lands cleanly."""
        if hasattr(content, "getCellNames"):
            paragraph = text_range.start[0]
            content._anchor = FakeRange(self, (paragraph, 0))
            content.after_paragraph = max(0, paragraph - 1)
            items = list(self.enumeration_items)
            if paragraph in items:
                items.insert(items.index(paragraph), content)
            else:
                items.append(content)
            self.enumeration_items = items
            if self.owner_document is not None:
                self.owner_document.tables.append(content)
            return
        self.insert_comment(text_range.start, text_range.end, content)

    owner_document = None

    def removeTextContent(self, content):
        """Dropping a comment drops its markers, never the text under them.

        A table goes altogether; a paragraph takes its line with it, and an
        index takes every paragraph it wrote.
        """
        if hasattr(content, "IsProtected") and hasattr(content, "IsVisible"):
            # The region goes; every paragraph it held stays where it was.
            if content in getattr(self, "sections", []):
                self.sections.remove(content)
            return
        if hasattr(content, "update") and hasattr(content, "IsProtected"):
            self.remove_index(content)
            return
        if hasattr(content, "Label") and hasattr(content, "kind"):
            # The mark goes, and the character it was.
            for index, portions in list(self.portions.items()):
                kept, offset, cut = [], 0, None
                for portion in portions:
                    text = portion.get("text", "") if isinstance(portion, dict) \
                        else ""
                    if isinstance(portion, dict) \
                            and portion.get("Footnote") is content:
                        cut = (offset, offset + len(text))
                        continue
                    kept.append(portion)
                    offset += len(text)
                if cut is not None:
                    self.portions[index] = kept
                    line = self.paragraphs[index]
                    self.paragraphs[index] = line[:cut[0]] + line[cut[1]:]
                    return
            raise RuntimeError("that note is not in this text")
        if hasattr(content, "PrimaryKey"):
            if content in getattr(self, "index_marks", []):
                self.index_marks.remove(content)
            return
        if hasattr(content, "getName") and hasattr(content, "setName") \
                and not hasattr(content, "getCellNames"):
            if hasattr(self, "bookmarks") and content in self.bookmarks:
                self.bookmarks.remove(content)
                return
        if hasattr(content, "getCellNames"):
            self.enumeration_items = [item for item in self.enumeration_items
                                      if item is not content]
            if self.owner_document is not None:
                self.owner_document.tables = [
                    table for table in self.owner_document.tables
                    if table is not content]
            return
        if hasattr(content, "index") and not isinstance(content, dict):
            index = content.index                    # a paragraph
            self._paragraph_removed(index)
            del self.paragraphs[index]
            del self.paragraph_ids[index]
            del self.styles[index]
            del self.outline_levels[index]
            self.portions.pop(index, None)
            self.portions = {(key - 1 if key > index else key): value
                             for key, value in self.portions.items()}
            self.enumeration_items = [
                item if not isinstance(item, int) else
                (item - 1 if item > index else item)
                for item in self.enumeration_items if item != index]
            return
        if hasattr(content, "getPresentation"):
            # A field carries the characters it shows, so taking it away
            # takes them out of the paragraph too.
            for index, portions in list(self.portions.items()):
                kept, offset, cut = [], 0, None
                for portion in portions:
                    text = portion.get("text", "") if isinstance(portion, dict) \
                        else ""
                    if isinstance(portion, dict) \
                            and portion.get("field") is content \
                            and portion.get("kind") == "TextField":
                        cut = (offset, offset + len(text))
                        continue
                    kept.append(portion)
                    offset += len(text)
                if cut is not None:
                    self.portions[index] = kept
                    line = self.paragraphs[index]
                    self.paragraphs[index] = line[:cut[0]] + line[cut[1]:]
                    return
            raise RuntimeError("that text content is not in this text")

        removed = False
        for index, portions in list(self.portions.items()):
            kept = []
            depth = None
            open_notes = []
            for portion in portions:
                kind = portion.get("kind", "Text") if isinstance(portion, dict) \
                    else "Text"
                if kind == "Annotation":
                    note = portion.get("field")
                    open_notes.append(note)
                    if note is content:
                        depth = len(open_notes)
                        removed = True
                        continue
                elif kind == "AnnotationEnd":
                    closing = len(open_notes)
                    if open_notes:
                        open_notes.pop()
                    if depth == closing:
                        depth = None
                        continue
                kept.append(portion)
            self.portions[index] = kept
        if not removed:
            raise RuntimeError("that text content is not in this text")

    def __init__(self, paragraphs, enumeration_items=None, styles=None,
                 outline_levels=None, expose_outline_level=True,
                 default_locale=None, portions=None):
        self.paragraphs = list(paragraphs)
        # Which paragraph is which, apart from where it stands: a held
        # paragraph object is this id, and it survives what Writer's does.
        self.paragraph_ids = list(range(len(self.paragraphs)))
        self._next_paragraph_id = len(self.paragraphs)
        self.styles = list(styles) if styles else ["Standard"] * len(self.paragraphs)
        self.outline_levels = (list(outline_levels) if outline_levels
                               else [0] * len(self.paragraphs))
        self.expose_outline_level = expose_outline_level
        self.default_locale = default_locale or ("en", "US")
        self.char_formatting = []
        self.breaks = {}
        # Character formatting applied to a **whole** paragraph lands on the
        # paragraph in Writer, not on the text: measured, a paragraph made
        # bold end to end answers DEFAULT_VALUE for CharWeight on its range
        # while the value reads 150. Kept here so a clean-up that ignored the
        # paragraph would fail in the tests as it failed in the office.
        self.paragraph_direct = {}
        self.border_formatting = []
        self.created_comments = []
        self.fills = {}
        self.portions = dict(portions) if portions else {}
        # Cursors handed out stay live in Writer: they move with the text and
        # collapse when it is rewritten. The ones here are tracked so the
        # same can happen — an anchor that quietly stayed valid would be a
        # fake kinder than the thing it stands for.
        self.held_cursors = []
        self.enumeration_items = (
            list(range(len(self.paragraphs)))
            if enumeration_items is None
            else list(enumeration_items)
        )

    def insertString(self, text_range, value, absorb):
        """insertString(range, text, bAbsorb) — the flag decides what happens.

        Measured on a real Writer: with absorb False the text goes in at the
        range's start and what was there stays, which is why "translate and
        replace" once produced both texts; with absorb True the range is
        rewritten.
        """
        start, end = sorted([text_range.start, text_range.end])
        self.replace_range(start, end if absorb else start, value)
        # A cursor is left *after* what it wrote, which is how a document is
        # built one insertString at a time; a fake that left it where it was
        # wrote the pieces of a caption in the wrong order.
        if hasattr(text_range, "mark") and "\n" not in value:
            text_range.mark = text_range.pos = (start[0],
                                                start[1] + len(value))

    def insert_comment(self, start, end, note):
        """What insertTextContent(range, annotation, True) does to the runs.

        The markers are threaded *into* whatever portions the paragraph
        already has: a second comment over the same words leaves the first
        alone, which is what makes a reply possible — a thread is several
        annotations over one stretch. Rebuilding the paragraph from its plain
        text instead, as this once did, silently destroyed the comment that
        was there, and a reply came back as the only comment in the document.
        """
        self.created_comments.append({"span": (start, end), "note": note})
        (paragraph, start_offset), (_, end_offset) = sorted([start, end])
        pieces = self.portions.get(paragraph)
        if pieces is None:
            pieces = [{"text": self.paragraphs[paragraph]}]

        pending = [(start_offset, {"kind": "Annotation", "text": "",
                                   "field": note}),
                   (end_offset, {"kind": "AnnotationEnd", "text": ""})]
        rebuilt = []
        offset = 0
        for piece in pieces:
            if piece.get("kind", "Text") != "Text":
                rebuilt.append(piece)           # a marker costs no characters
                continue
            text = piece.get("text", "")
            taken = 0
            while pending and offset <= pending[0][0] <= offset + len(text):
                at, marker = pending.pop(0)
                cut = at - offset
                if cut > taken:
                    rebuilt.append(dict(piece, text=text[taken:cut]))
                    taken = cut
                rebuilt.append(marker)
            if taken < len(text):
                rebuilt.append(dict(piece, text=text[taken:]))
            offset += len(text)
        rebuilt.extend(marker for _, marker in pending)
        self.portions[paragraph] = [
            piece for piece in rebuilt
            if piece.get("kind", "Text") != "Text" or piece.get("text")]

    def char_state(self, start, end, name):
        """DIRECT_VALUE, AMBIGUOUS_VALUE or DEFAULT_VALUE for a span.

        A property the paragraph carries reads as DEFAULT on the text, which
        is what a real Writer answers when the formatting was applied to the
        whole paragraph.
        """
        (first, from_offset), (last, to_offset) = sorted([start, end])
        if name in self.paragraph_direct.get(first, {}) and first == last:
            return "DEFAULT_VALUE"
        carried, total = 0, 0
        for paragraph in range(first, last + 1):
            if paragraph >= len(self.paragraphs):
                break
            body = self.paragraphs[paragraph]
            starts = from_offset if paragraph == first else 0
            ends = to_offset if paragraph == last else len(body)
            offset = 0
            for text, _locale, properties, kind, _field in \
                    self.portions_of(paragraph):
                if kind == "Text" and text:
                    covered = max(0, min(offset + len(text), ends)
                                  - max(offset, starts))
                    if covered:
                        total += covered
                        if name in properties:
                            carried += covered
                offset += len(text)
        if not total or not carried:
            return "DEFAULT_VALUE"
        return "DIRECT_VALUE" if carried == total else "AMBIGUOUS_VALUE"

    def clear_char_property(self, start, end, name):
        """Take a character property off a span, as setPropertyToDefault does"""
        (first, _), (last, _) = sorted([start, end])
        if first == last:
            self.paragraph_direct.get(first, {}).pop(name, None)
        for paragraph in range(first, last + 1):
            declared = self.portions.get(paragraph)
            if not declared:
                continue
            self.portions[paragraph] = [
                {key: value for key, value in dict(piece).items()
                 if key != name} if isinstance(piece, dict) else piece
                for piece in declared]

    def paragraph_state(self, index, name):
        if name in ("FillStyle", "FillColor"):
            return ("DIRECT_VALUE" if name in self.fills.get(index, {})
                    else "DEFAULT_VALUE")
        if name in ("BreakType", "PageDescName", "PageNumberOffset"):
            return ("DIRECT_VALUE" if name in self.breaks.get(index, {})
                    else "DEFAULT_VALUE")
        return ("DIRECT_VALUE" if name in self.paragraph_direct.get(index, {})
                else "DEFAULT_VALUE")

    def clear_paragraph_property(self, index, name):
        self.fills.get(index, {}).pop(name, None)
        self.breaks.get(index, {}).pop(name, None)
        if self.paragraph_direct.get(index, {}).pop(name, None) is not None:
            # It was applied to the whole paragraph, so it is on the text too.
            self.clear_char_property((index, 0),
                                     (index, len(self.paragraphs[index])),
                                     name)

    def record_border_property(self, start, end, name, value):
        self.border_formatting.append({"span": (start, end), name: value})

    def border_property(self, start, end, name):
        for applied in reversed(self.border_formatting):
            if applied["span"] == (start, end) and name in applied:
                return applied[name]
        return None

    def record_char_property(self, start, end, name, value):
        """Apply a character property to a span, and remember it.

        Recording it alone was enough while only the tests looked at it, but
        a hyperlink has to be visible where a real one is — on the portions —
        or nothing walking the document could find it. So the portions of the
        span are split and given the property, exactly as Writer splits a
        run when part of it is made bold.
        """
        self.char_formatting.append({"span": (start, end), name: value})
        self.apply_char_property(start, end, name, value)
        (first, from_offset), (last, to_offset) = sorted([start, end])
        if first == last and from_offset == 0 \
                and to_offset >= len(self.paragraphs[first]):
            self.paragraph_direct.setdefault(first, {})[name] = value

    def apply_char_property(self, start, end, name, value):
        """Split the portions of a span and give them a property."""
        (first, start_offset), (last, end_offset) = sorted([start, end])
        for paragraph in range(first, last + 1):
            if paragraph >= len(self.paragraphs):
                break
            body = self.paragraphs[paragraph]
            from_offset = start_offset if paragraph == first else 0
            to_offset = end_offset if paragraph == last else len(body)
            if from_offset >= to_offset:
                continue
            rebuilt, position = [], 0
            for text, locale, properties, kind, field in \
                    self.portions_of(paragraph):
                if kind in ("TextField", "Footnote"):
                    carrier = "Footnote" if kind == "Footnote" else "field"
                    rebuilt.append({"text": text, "kind": kind,
                                    carrier: field, "locale": locale,
                                    **properties})
                    position += len(text)
                    continue
                if kind != "Text":
                    rebuilt.append({"kind": kind, "text": "", "field": field})
                    continue
                for index, character in enumerate(text, start=position):
                    inside = from_offset <= index < to_offset
                    piece = {"text": character, "locale": locale,
                             **properties}
                    if inside:
                        piece[name] = value
                    previous = rebuilt[-1] if rebuilt else None
                    if previous is not None \
                            and previous.get("kind", "Text") == "Text" \
                            and {k: v for k, v in previous.items()
                                 if k != "text"} == {k: v for k, v
                                                     in piece.items()
                                                     if k != "text"}:
                        previous["text"] += character
                    else:
                        rebuilt.append(piece)
                position += len(text)
            self.portions[paragraph] = [
                piece for piece in rebuilt
                if piece.get("kind", "Text") != "Text" or piece.get("text")]

    def char_property(self, start, end, name):
        """The last value applied to this span for a property, else None."""
        for applied in reversed(self.char_formatting):
            if applied["span"] == (start, end) and name in applied:
                return applied[name]
        return None

    def set_style(self, start, end, style):
        """A paragraph's style, and the outline level that comes with it.

        Applying "Heading 2" in Writer makes the paragraph a level-2 heading
        — the style carries the level — so a table of contents built here
        lists it. A fake that changed only the name left the outline behind.
        """
        (start_para, _), (end_para, _) = sorted([start, end])
        level = 0
        if style.startswith("Heading "):
            suffix = style[len("Heading "):].strip()
            level = int(suffix) if suffix.isdigit() else 0
        for paragraph in range(start_para, end_para + 1):
            self.styles[paragraph] = style
            self.outline_levels[paragraph] = level

    def locale_at(self, position):
        """The locale of the portion holding a position."""
        paragraph, offset = position
        for text, locale, _, _kind, _field in self.portions_of(paragraph):
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
            for text, existing, properties, kind, field in \
                    self.portions_of(paragraph):
                if kind in ("TextField", "Footnote"):
                    # These carry their characters, so they keep them — and
                    # the thing they carry, under its own name.
                    carrier = "Footnote" if kind == "Footnote" else "field"
                    rebuilt.append({"kind": kind, "text": text,
                                    carrier: field})
                    position += len(text)
                    continue
                if kind != "Text":
                    # A comment marker is not text: marking a language must
                    # not sweep it away, or a fake would hide a rewrite that
                    # keeps a comment.
                    rebuilt.append({"kind": kind, "text": "", "field": field})
                    continue
                for index, character in enumerate(text, start=position):
                    marked = from_offset <= index < to_offset
                    chosen = locale if marked else existing
                    previous = rebuilt[-1] if rebuilt else None
                    if previous is not None and previous.get("kind") == "Text" \
                            and previous["locale"] is chosen \
                            and previous["properties"] == properties:
                        previous["text"] += character
                    else:
                        rebuilt.append({"kind": "Text", "text": character,
                                        "locale": chosen,
                                        "properties": properties})
                position += len(text)
            self.portions[paragraph] = [
                entry if entry["kind"] != "Text"
                else {"text": entry["text"], "locale": entry["locale"],
                      **entry["properties"]}
                for entry in rebuilt]

    def portions_of(self, paragraph):
        """
        The runs of a paragraph, one run unless told otherwise

        A run is either a (text, locale) pair or a dict with text, locale and
        whatever character properties the test cares about.
        """
        declared = self.portions.get(paragraph)
        if declared is None:
            return [(self.paragraphs[paragraph],
                     FakeLocale(*self.default_locale), {}, "Text", None)]
        normalised = []
        for run in declared:
            if isinstance(run, dict):
                properties = {k: v for k, v in run.items()
                              if k not in ("text", "locale", "kind", "field",
                                           "redline", "is_start")}
                # A recorded change marks its text with empty portions of type
                # "Redline", carrying the change's type, author and identifier,
                # with IsStart saying which end this is — measured, and the
                # same shape as a comment's markers.
                recorded = run.get("redline")
                if recorded:
                    properties.update({
                        "RedlineType": recorded.get("type", "Insert"),
                        "RedlineAuthor": recorded.get("author", ""),
                        "RedlineIdentifier": recorded.get("id", ""),
                        "IsStart": bool(run.get("is_start", False))})
                normalised.append((run.get("text", ""),
                                   run.get("locale",
                                           FakeLocale(*self.default_locale)),
                                   properties,
                                   run.get("kind", "Text"),
                                   # A footnote's mark carries the note under
                                   # its own name, the way a field carries
                                   # the field.
                                   run.get("field", run.get("Footnote"))))
            else:
                text, locale = run
                normalised.append((text, locale, {}, "Text", None))
        return normalised

    def _own(self, text_range):
        """Writer throws when a range from another text is passed in.

        The range answers getText() with the cell when it lives in one, so
        ownership is asked of the model behind it rather than of that.
        """
        if getattr(text_range, "model", None) is not self:
            raise RuntimeError(
                "End of content node doesn't have the proper start node")

    def _collapse_cursors_within(self, start, end, keeping=None):
        """A held cursor whose text is rewritten collapses where it stood.

        Measured on a real Writer: it does not throw and it does not follow
        the new text — it is left as an empty position, which is why an
        anchor remembers what it covered when it was made.
        """
        low, high = sorted([start, end])
        for cursor in self.held_cursors:
            if cursor is keeping or cursor.start == cursor.end:
                continue
            if low <= cursor.start and cursor.end <= high:
                cursor.mark = cursor.pos = low

    def _move_cursors(self, rule, keeping=None):
        """Move every held cursor as Writer does when the text moves.

        Every UNO cursor is kept up to date by the document; a fake whose
        cursors stood still let an anchor look right after the paragraphs
        around it had moved, which is the one thing anchors exist for.
        """
        for cursor in self.held_cursors:
            if cursor is keeping:
                continue
            cursor.mark = rule(*cursor.mark)
            cursor.pos = rule(*cursor.pos)

    def _paragraph_removed(self, index):
        """Cursors in a removed paragraph empty; those below it move up.

        Which is the point of an anchor: a paragraph taken away above it
        changes its number and not the text it points at. Called while the
        paragraph is still there, so what survives it is one shorter.
        """
        left = max(0, min(index, len(self.paragraphs) - 2))
        for cursor in self.held_cursors:
            moved = []
            for paragraph, offset in (cursor.mark, cursor.pos):
                if paragraph == index:
                    moved.append((left, 0))
                elif paragraph > index:
                    moved.append((paragraph - 1, offset))
                else:
                    moved.append((paragraph, offset))
            cursor.mark, cursor.pos = moved

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

    def replace_range(self, start, end, value, writer=None):
        """
        Rewrite the span, joining paragraphs when the span crosses a break

        Declared portions for the paragraphs touched are dropped: they
        described the old text, and keeping them would let a test read back
        runs that no longer exist. What the new text looks like is a question
        only a live LibreOffice answers, so the read-after-write round trip is
        checked in tests/live/writer_tools_check.py instead.

        Comment markers *outside* the replaced span survive, as they do in
        Writer: that is what lets a rewrite leave a comment on text it did
        not change. Markers inside the span are destroyed, which is what
        makes rewriting commented text lose the comment.
        """
        self._collapse_cursors_within(start, end, keeping=writer)
        (first, _), (last, _) = sorted([start, end])
        for paragraph in range(first, last + 1):
            declared = self.portions.pop(paragraph, None)
            if declared is None or paragraph != first or first != last:
                continue
            kept = self._markers_outside(declared, start[1], end[1], value)
            if kept is not None:
                self.portions[paragraph] = kept
        (start_para, start_offset), (end_para, end_offset) = sorted([start, end])
        grown = len(value) - (end_offset - start_offset)
        if start_para == end_para:
            paragraph = self.paragraphs[start_para]
            self.paragraphs[start_para] = (
                paragraph[:start_offset] + value + paragraph[end_offset:])
            # What stood after the rewritten stretch moves with it.
            self._move_cursors(lambda where, offset:
                               (where, offset + grown)
                               if where == start_para and offset > end_offset
                               or (where == start_para and offset == end_offset
                                   and end_offset > start_offset)
                               else (where, offset), keeping=writer)
            return
        merged = end_para - start_para
        landing = start_offset + len(value)

        def across(where, offset):
            if where > end_para:
                return (where - merged, offset)
            if where == end_para and offset >= end_offset:
                return (start_para, landing + offset - end_offset)
            if where > start_para or (where == start_para
                                      and offset > start_offset):
                return (start_para, start_offset)
            return (where, offset)

        self._move_cursors(across, keeping=writer)
        head = self.paragraphs[start_para][:start_offset]
        tail = self.paragraphs[end_para][end_offset:]
        self.paragraphs[start_para:end_para + 1] = [head + value + tail]
        # Measured: a paragraph merged into the one before it is gone, and
        # its object throws; the first keeps its identity.
        del self.paragraph_ids[start_para + 1:end_para + 1]
        del self.styles[start_para + 1:end_para + 1]
        del self.outline_levels[start_para + 1:end_para + 1]
        self.enumeration_items = list(range(len(self.paragraphs)))

    def _markers_outside(self, declared, start_offset, end_offset, value):
        """The paragraph's portions after a span was replaced, markers kept.

        Returns None when the span cannot be described this way, so the
        caller falls back to dropping the portions.
        """
        start_offset, end_offset = sorted([start_offset, end_offset])
        rebuilt = []
        offset = 0
        wrote = False
        for portion in declared:
            if not isinstance(portion, dict):
                return None
            kind = portion.get("kind", "Text")
            text = portion.get("text", "")
            if kind in ("TextField", "Footnote"):
                # A field and a footnote's mark carry the characters they
                # show, so they take part in this arithmetic rather than
                # sitting between characters — and one dies when the
                # characters it is made of are replaced. Measured: a rewrite
                # over a footnote's mark takes the note with it, while one
                # that stops at the mark leaves it.
                if not (start_offset < offset + len(text)
                        and end_offset > offset):
                    rebuilt.append(portion)
                offset += len(text)
                continue
            if kind != "Text":
                # Measured on a live LibreOffice, and it is the arithmetic of
                # a half-open deleted range. An opening Annotation dies when
                # it sits in the characters that go, start included and end
                # excluded — so rewriting the commented word destroys its
                # comment outright, while a stretch that *ends* where an
                # anchor begins is harmless. An AnnotationEnd dies at either
                # boundary, which is why a rewrite of the stretch right after
                # a comment took the comment with it.
                if kind == "AnnotationEnd":
                    doomed = start_offset <= offset <= end_offset
                else:
                    doomed = start_offset <= offset < end_offset
                if doomed:
                    continue
                rebuilt.append(portion)
                continue
            head = text[:max(0, min(len(text), start_offset - offset))]
            tail = text[max(0, min(len(text), end_offset - offset)):]
            if head:
                rebuilt.append(dict(portion, text=head))
            if not wrote and offset + len(text) >= start_offset:
                rebuilt.append({"text": value})
                wrote = True
            if tail:
                rebuilt.append(dict(portion, text=tail))
            offset += len(text)
        if not wrote:
            rebuilt.append({"text": value})
        return [portion for portion in rebuilt
                if portion.get("kind", "Text") != "Text" or portion.get("text")]

    def compareRegionStarts(self, first, second):
        """1 when `first` starts before `second`, 0 equal, -1 after."""
        self._own(first)
        self._own(second)
        left, right = first.start, second.start
        return 1 if left < right else (0 if left == right else -1)

    def createTextCursorByRange(self, text_range):
        """A cursor over the whole range, not a caret at its start.

        Measured: createTextCursorByRange over a paragraph answers with that
        paragraph's text. The fake used to collapse, which would have let an
        anchor over a phrase look empty the moment it was made.
        """
        self._own(text_range)
        cursor = FakeTextCursor(self, text_range.start, text_range.end)
        self.held_cursors.append(cursor)
        return cursor

    def createEnumeration(self):
        # The table fakes stand on this module, so the import is made here
        # rather than at the top, where it would close a circle.
        from tests.fakes_tables import FakeTextTable

        items = []
        for item in self.enumeration_items:
            if item == "table":
                items.append(FakeTextTable())
            elif isinstance(item, FakeTextTable):
                items.append(item)
            else:
                items.append(FakeParagraph(self, item))
        return FakeEnumeration(items)

    def compareRegionStarts(self, range1, range2):
        """0 when both start at the same spot; the sign convention is unused."""
        self._own(range1)
        self._own(range2)
        if range1.start == range2.start:
            return 0
        return 1 if range1.start < range2.start else -1

    def compareRegionEnds(self, range1, range2):
        """1 when the first ends before the second, 0 equal, -1 after.

        The same convention as compareRegionStarts, and the pair is what
        says whether one range is inside another — which is how a protected
        section is recognised.
        """
        self._own(range1)
        self._own(range2)
        if range1.end == range2.end:
            return 0
        return 1 if range1.end < range2.end else -1
