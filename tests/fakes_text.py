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

    def insertTextContent(self, text_range, content, absorb):
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

        A table goes altogether; a paragraph takes its line with it.
        """
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
        self.styles = list(styles) if styles else ["Standard"] * len(self.paragraphs)
        self.outline_levels = (list(outline_levels) if outline_levels
                               else [0] * len(self.paragraphs))
        self.expose_outline_level = expose_outline_level
        self.default_locale = default_locale or ("en", "US")
        self.char_formatting = []
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
                                   run.get("field")))
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
