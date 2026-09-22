"""Captions and cross-references: the numbers a document keeps for itself.

A caption is the pair "Figure 2: The schema" where the 2 is a *sequence
field* that counts itself, and a cross-reference is a field that shows what
some other place says. Both were measured before they were built:

  * a caption's number is a `com.sun.star.text.TextField.SetExpression`
    attached to a **sequence master**, `com.sun.star.text.FieldMaster.
    SetExpression.<Category>`. A fresh document already carries five —
    Illustration, Table, Text, Drawing, Figure — and the element names come
    back spelled `fieldmaster` in lower case while `getByName` takes the
    `FieldMaster` spelling. A category nobody has yet needs a master made and
    `SubType = SEQUENCE`;
  * `SequenceValue` is an **identity, not a number**: three captions made in
    the order 1, 2, 3 and then a fourth put in front of them read 2, 0, 1, 3
    while showing 2, 3, 4, 1. So a reference is bound to a caption by its
    SequenceValue and the number it shows follows the document — measured,
    including the reference that read "Figure 2" before and "Figure 3" after;
  * a reference to a heading is a reference to a **bookmark**. Writer's own
    dialog leaves one named `__RefHeading__…` over the heading, and a
    bookmark made here works exactly the same way;
  * a reference whose target is not there shows "Error: Reference source not
    found" in the text of the document, in the office's language. Nothing
    checks it afterwards, so the target is checked before the field is
    written;
  * `ONLY_CAPTION` gives what follows the number with a separator of ": "
    eaten and one of " – " left in place ("– A dashed caption"), which is
    Writer's own parsing and not something to work around;
  * where a caption paragraph can go was measured too. A paragraph break at
    the **end** of a paragraph leaves an empty paragraph directly after it —
    before a table that follows it, not after — and one at the **start** of a
    paragraph leaves an empty paragraph directly before it, after any table
    that precedes it. A table that opens the document therefore has nowhere
    above it for a caption to go, and that is refused rather than written
    below it.
"""

from typing import Any, Dict, List, Optional
import logging

from uno_values import (AddressError, _get_property, _supports, _text_payload,
                        refusal)

logger = logging.getLogger(__name__)

SEQUENCE_SERVICE = "com.sun.star.text.TextField.SetExpression"
REFERENCE_SERVICE = "com.sun.star.text.TextField.GetReference"
MASTER_PREFIX = "com.sun.star.text.fieldmaster.setexpression."

# com.sun.star.text.ReferenceFieldSource, measured.
SOURCE_OF_KIND = {"reference_mark": 0, "caption": 1, "bookmark": 2,
                  "footnote": 3, "endnote": 4}
KIND_OF_SOURCE = {value: key for key, value in SOURCE_OF_KIND.items()}

# com.sun.star.text.ReferenceFieldPart, in words a caller can read.
PART_OF_NAME = {"page": 0, "chapter": 1, "text": 2, "above_below": 3,
                "page_style": 4, "category_and_number": 5,
                "caption_text": 6, "number": 7, "heading_number": 8,
                "number_no_context": 9, "number_full_context": 10}
NAME_OF_PART = {value: key for key, value in PART_OF_NAME.items()}

# com.sun.star.style.NumberingType, measured: 1 shows "a", 2 shows "I".
NUMBERING_OF_NAME = {"arabic": 4, "roman_upper": 2, "roman_lower": 3,
                     "letter_upper": 0, "letter_lower": 1}

# Writer's own heading cross-references point at a mark named like this.
# A bookmark given such a name turns into one and is then invisible to
# getBookmarks(), which is why nothing here makes one — see _heading_bookmark.
WRITER_HEADING_MARK = "__RefHeading__"


class ReferencesMixin:
    """Part of UNOBridge — see uno_bridge.py for how the parts meet."""

    # ---- what is in the document ------------------------------------

    def _fields_of_service(self, doc: Any, service: str) -> List[Any]:
        """Every field of one kind, in the order getTextFields() gives them"""
        found = []
        try:
            fields = doc.getTextFields().createEnumeration()
        except Exception as e:
            logger.error(f"Could not enumerate the fields: {e}")
            return found
        while fields.hasMoreElements():
            field = fields.nextElement()
            if _supports(field, service):
                found.append(field)
        return found

    def _master_category(self, field: Any) -> Optional[str]:
        """The sequence a caption's number counts in — "Figure", "Table"."""
        master = _get_property(field, "TextFieldMaster", None)
        name = _get_property(master, "Name", None) if master else None
        if name:
            return name
        full = _get_property(master, "InstanceName", "") if master else ""
        if full and full.lower().startswith(MASTER_PREFIX):
            return full[len(MASTER_PREFIX):]
        return None

    def _captions(self, doc: Any) -> List[Dict[str, Any]]:
        """Every caption number in the document, placed in one walk"""
        fields = self._fields_of_service(doc, SEQUENCE_SERVICE)
        anchors, kept = [], []
        for field in fields:
            try:
                anchors.append(field.getAnchor())
            except Exception as e:
                logger.info(f"A caption would not say where it is: {e}")
                continue
            kept.append(field)
        placed = self._addresses_in_order(doc, anchors)

        # A range's own text is the whole body, so the caption's line comes
        # from the paragraph its address names — gathered in one walk, since
        # reaching a paragraph by index is a walk of its own.
        lines = self._paragraph_texts(
            doc, {(one or {}).get("paragraph") for one in placed})

        found = []
        for field, anchor, address in zip(kept, anchors, placed):
            category = self._master_category(field)
            number = None
            try:
                number = field.getPresentation(False)
            except Exception as e:
                logger.info(f"A caption would not say its number: {e}")
            paragraph = _text_payload(
                lines.get((address or {}).get("paragraph"), ""))["text"]
            found.append({
                "kind": "caption",
                "category": category,
                "number": number,
                "sequence_id": _get_property(field, "SequenceValue", None),
                "text": paragraph,
                "address": address,
                "reference": {"caption": f"{category} {number}"},
                "field": field,
            })
        # getTextFields() hands them out in the order they were made, not the
        # order they stand in: a caption put in front of another comes last,
        # while the numbers it renumbered read in document order. Every
        # caller wants the page's order.
        found.sort(key=lambda one: (
            (one["address"] or {}).get("paragraph")
            if (one["address"] or {}).get("paragraph") is not None else 10 ** 9,
            (one["address"] or {}).get("offset") or 0))
        return found

    def _paragraph_texts(self, doc: Any, wanted: Any) -> Dict[int, str]:
        """The text of the paragraphs named, in one walk of the body"""
        wanted = {one for one in wanted if one is not None}
        found: Dict[int, str] = {}
        if not wanted:
            return found
        for paragraph, index in self._body_paragraphs(doc):
            if index in wanted:
                found[index] = paragraph.getString()
                if len(found) == len(wanted):
                    break
        return found

    def _reference_marks(self, doc: Any) -> List[Dict[str, Any]]:
        """The reference marks — Writer's "Set Reference" targets"""
        try:
            marks = doc.getReferenceMarks()
        except Exception as e:
            logger.info(f"This document keeps no reference marks: {e}")
            return []
        names, anchors = [], []
        for name in marks.getElementNames():
            try:
                anchors.append(marks.getByName(name).getAnchor())
            except Exception as e:
                logger.info(f"Could not read reference mark {name}: {e}")
                continue
            names.append(name)
        placed = self._addresses_in_order(doc, anchors)
        return [{"kind": "reference_mark", "name": name,
                 "text": _text_payload(anchor.getString())["text"],
                 "address": address,
                 "reference": {"reference_mark": name}}
                for name, anchor, address in zip(names, anchors, placed)]

    def _bookmarks_by_paragraph(self, doc: Any) -> Dict[int, List[Dict]]:
        """The bookmarks of the document, gathered by the paragraph they are in"""
        gathered: Dict[int, List[Dict]] = {}
        for one in self.list_bookmarks(number=True,
                                       doc=doc).get("bookmarks", []):
            address = one.get("address") or {}
            if address.get("paragraph") is None:
                continue
            gathered.setdefault(address["paragraph"], []).append(one)
        return gathered

    def _bookmark_over(self, listed: List[Dict], text: str) -> Optional[str]:
        """The name of a bookmark covering the whole of that text, if there is one"""
        for one in listed:
            if one.get("text") and one["text"] == text:
                return one["name"]
        return None

    def _headings_as_targets(self, doc: Any) -> List[Dict[str, Any]]:
        """Every heading, with the bookmark a reference to it would use"""
        from uno_values import _heading_level

        by_paragraph = self._bookmarks_by_paragraph(doc)
        found = []
        for paragraph, index in self._body_paragraphs(doc):
            level = _heading_level(paragraph)
            if level <= 0:
                continue
            text = paragraph.getString()
            found.append({
                "kind": "heading", "level": level,
                "text": _text_payload(text)["text"],
                "address": {"paragraph": index},
                "bookmark": self._bookmark_over(by_paragraph.get(index, []),
                                                text),
                "reference": {"heading": index},
            })
        return found

    # ---- listing -----------------------------------------------------

    def list_reference_targets(self, kinds: Optional[List[str]] = None,
                               address: Any = None,
                               doc: Any = None) -> Dict[str, Any]:
        """
        What a cross-reference can point at: headings, captions, bookmarks,
        reference marks

        Every target carries `reference`, which is what insert_cross_reference
        takes as its `target`, so nothing has to be spelled out by hand.
        """
        doc, error = self._writer_document(doc, "Listing reference targets")
        if error:
            return error

        wanted = kinds or ["heading", "caption", "bookmark", "reference_mark"]
        unknown = [one for one in wanted
                   if one not in ("heading", "caption", "bookmark",
                                  "reference_mark")]
        if unknown:
            return refusal("INVALID_PARAMETER",
                           f"a target is a heading, a caption, a bookmark or "
                           f"a reference_mark; got {unknown[0]!r}")

        try:
            covers, scope = self._comment_scope(doc, address)
        except Exception as e:
            return refusal("INVALID_ADDRESS", e)

        targets: List[Dict[str, Any]] = []
        if "heading" in wanted:
            targets.extend(self._headings_as_targets(doc))
        if "caption" in wanted:
            targets.extend({key: value for key, value in one.items()
                            if key != "field"} for one in self._captions(doc))
        if "bookmark" in wanted:
            # Targets are listed in reading order and scoped by paragraph,
            # so here the numbers are what is wanted.
            listed = self.list_bookmarks(number=True, doc=doc)
            for one in listed.get("bookmarks", []):
                targets.append({"kind": "bookmark", "name": one["name"],
                                "text": one["text"], "address": one["address"],
                                "reference": {"bookmark": one["name"]}})
        if "reference_mark" in wanted:
            targets.extend(self._reference_marks(doc))

        targets = [one for one in targets if covers(one["address"])]
        targets.sort(key=lambda one: ((one["address"] or {}).get("paragraph")
                                      if (one["address"] or {}).get("paragraph")
                                      is not None else 10 ** 9,
                                      (one["address"] or {}).get("offset") or 0))
        counted: Dict[str, int] = {}
        for one in targets:
            counted[one["kind"]] = counted.get(one["kind"], 0) + 1
        return {"success": True, "targets": targets, "count": len(targets),
                "kinds": counted, "scope": scope}

    def _target_of_reference(self, field: Any) -> Dict[str, Any]:
        source = _get_property(field, "ReferenceFieldSource", None)
        kind = KIND_OF_SOURCE.get(source, "unknown")
        described = {"kind": kind,
                     "name": _get_property(field, "SourceName", None)}
        if kind == "caption":
            described["sequence_id"] = _get_property(field, "SequenceNumber",
                                                     None)
        return described

    def list_references(self, address: Any = None,
                        doc: Any = None) -> Dict[str, Any]:
        """
        The cross-references of a document, each with what it points at

        A reference whose target is gone says so in `broken` — Writer shows
        "Error: Reference source not found" in the text itself, in whatever
        language the office speaks, and nothing else in the document says
        which reference it was.
        """
        doc, error = self._writer_document(doc, "Listing cross-references")
        if error:
            return error

        try:
            covers, scope = self._comment_scope(doc, address)
        except Exception as e:
            return refusal("INVALID_ADDRESS", e)

        fields = self._fields_of_service(doc, REFERENCE_SERVICE)
        anchors, kept = [], []
        for field in fields:
            try:
                anchors.append(field.getAnchor())
            except Exception as e:
                logger.info(f"A reference would not say where it is: {e}")
                continue
            kept.append(field)
        placed = self._addresses_in_order(doc, anchors)

        known = self._known_targets(doc)
        references = []
        for field, located in zip(kept, placed):
            target = self._target_of_reference(field)
            part = _get_property(field, "ReferenceFieldPart", None)
            shows = None
            try:
                shows = field.getPresentation(False)
            except Exception as e:
                logger.info(f"A reference would not say what it shows: {e}")
            described = {
                "target": target,
                "part": NAME_OF_PART.get(part, part),
                "shows": shows,
                "address": located,
                "broken": not self._target_is_there(target, known),
            }
            if not covers(described["address"]):
                continue
            references.append(described)

        return {"success": True, "references": references,
                "count": len(references),
                "broken": sum(1 for one in references if one["broken"]),
                "scope": scope}

    def _known_targets(self, doc: Any) -> Dict[str, Any]:
        """What the references of this document could be pointing at"""
        marks = self._bookmarks(doc)
        try:
            reference_marks = set(doc.getReferenceMarks().getElementNames())
        except Exception as e:
            logger.info(f"This document keeps no reference marks: {e}")
            reference_marks = set()
        captions = {}
        for one in self._captions(doc):
            captions.setdefault(one["category"], set()).add(one["sequence_id"])
        return {
            "bookmark": set(marks.getElementNames()) if marks else set(),
            "reference_mark": reference_marks,
            "caption": captions,
        }

    def _target_is_there(self, target: Dict[str, Any],
                         known: Dict[str, Any]) -> bool:
        kind, name = target.get("kind"), target.get("name")
        if kind == "caption":
            return target.get("sequence_id") in known["caption"].get(name,
                                                                     set())
        if kind in ("bookmark", "reference_mark"):
            return name in known[kind]
        return True                 # footnotes and endnotes are not ours yet

    # ---- writing -----------------------------------------------------

    def _sequence_master(self, doc: Any, category: str) -> Any:
        """The master a caption's number counts against, made if it is new"""
        masters = doc.getTextFieldMasters()
        name = "com.sun.star.text.FieldMaster.SetExpression." + category
        if masters.hasByName(name):
            return masters.getByName(name)
        master = doc.createInstance(
            "com.sun.star.text.FieldMaster.SetExpression")
        master.Name = category
        try:
            from com.sun.star.text.SetVariableType import SEQUENCE
            master.SubType = SEQUENCE
        except Exception as e:
            logger.info(f"Could not make {category} a sequence: {e}")
        return master

    def _caption_place(self, doc: Any, address: Any, table: Optional[str],
                       position: str) -> int:
        """Make an empty paragraph for the caption, and say which it is.

        Measured: a break at the **end** of a paragraph leaves the new one
        directly after it — before any table that follows — and one at the
        **start** leaves it directly before, after any table in between. The
        cursor that inserts the break is left in the *old* paragraph in the
        second case, so the caption is written through a fresh cursor into
        the paragraph named here rather than through that one.
        """
        from com.sun.star.text.ControlCharacter import PARAGRAPH_BREAK

        body = doc.getText()
        if table is not None:
            where = self._table_positions(doc).get(table)
            if where is None:
                raise AddressError(f"no table called {table!r}; list_tables "
                                   f"says which there are")
            if position == "below":
                after = self._paragraph_at(body, where)
                if after is None:
                    raise AddressError(
                        "there is no paragraph below this table for a caption "
                        "to go in")
                at = after.getStart()
            else:
                if where == 0:
                    raise AddressError(
                        "this table opens the document, so there is no "
                        "paragraph above it; a caption can go below it")
                at = self._paragraph_at(body, where - 1).getEnd()
            index = where
        else:
            spot = self._paragraph_index_of(doc, address)
            paragraph = self._paragraph_at(body, spot)
            if paragraph is None:
                raise AddressError(f"no body paragraph {spot}")
            below = position == "below"
            at = paragraph.getEnd() if below else paragraph.getStart()
            index = spot + 1 if below else spot

        body.insertControlCharacter(body.createTextCursorByRange(at),
                                    PARAGRAPH_BREAK, False)
        return index

    def insert_caption(self, text: str, address: Any = None,
                       table: Optional[str] = None,
                       image: Optional[str] = None,
                       category: str = "Figure", position: str = "below",
                       numbering: str = "arabic", separator: str = ": ",
                       style: Optional[str] = None,
                       track_changes: Optional[bool] = None,
                       doc: Any = None) -> Dict[str, Any]:
        """
        Number a picture, a table or a paragraph with a caption of its own

        The number is a field that counts itself, so a caption put in front of
        another renumbers everything after it — and the references to them
        follow.
        """
        doc, error = self._writer_document(doc, "Adding a caption")
        if error:
            return error
        if position not in ("below", "above"):
            return refusal("INVALID_PARAMETER",
                           f"position is \"below\" or \"above\", got "
                           f"{position!r}")
        if numbering not in NUMBERING_OF_NAME:
            return refusal("INVALID_PARAMETER",
                           f"numbering is one of "
                           f"{', '.join(sorted(NUMBERING_OF_NAME))}, got "
                           f"{numbering!r}")
        if not isinstance(category, str) or not category.strip():
            return refusal("INVALID_PARAMETER",
                           "a caption needs a category, such as \"Figure\" or "
                           "\"Table\"")
        category = category.strip()
        named = [one for one in (address is not None, table is not None,
                                 image is not None) if one]
        if len(named) != 1:
            return refusal("INVALID_PARAMETER",
                           "say what is being captioned in exactly one way: "
                           "address for a paragraph, table for a table by "
                           "name, or image for a picture by name")

        if image is not None:
            found = self.list_images(doc=doc)
            if not found.get("success"):
                return found
            picture = next((one for one in found["images"]
                            if one.get("name") == image), None)
            if picture is None:
                return refusal("NOT_FOUND",
                               f"no picture called {image!r}; list_images says "
                               f"which there are")
            address = picture.get("address")
            if not address or address.get("paragraph") is None:
                return refusal("UNSUPPORTED",
                               f"{image!r} is not anchored in the body text, "
                               f"so a caption cannot be placed beside it")
            address = {"paragraph": address["paragraph"]}

        chosen = style or self._caption_style(doc, category)

        def edit():
            body = doc.getText()
            index = self._caption_place(doc, address, table, position)
            paragraph = self._paragraph_at(body, index)
            cursor = body.createTextCursorByRange(paragraph.getStart())
            if chosen:
                try:
                    cursor.ParaStyleName = chosen
                except Exception as e:
                    logger.info(f"Could not apply the style {chosen}: {e}")
            body.insertString(cursor, category + " ", False)
            number = doc.createInstance(SEQUENCE_SERVICE)
            number.attachTextFieldMaster(self._sequence_master(doc, category))
            number.Content = category + "+1"
            number.NumberingType = NUMBERING_OF_NAME[numbering]
            body.insertTextContent(cursor, number, False)
            if text:
                body.insertString(cursor, separator + text, False)
            doc.getTextFields().refresh()
            shown = number.getPresentation(False)
            return {"category": category, "number": shown,
                    "sequence_id": _get_property(number, "SequenceValue", None),
                    "address": {"paragraph": index},
                    "text": self._paragraph_at(body, index).getString(),
                    "paragraph_style": _get_property(cursor, "ParaStyleName",
                                                     None),
                    "reference": {"caption": f"{category} {shown}"}}

        return self._guarded_edit(doc, f"MCP: caption a {category.lower()}",
                                  track_changes, edit)

    def _caption_style(self, doc: Any, category: str) -> Optional[str]:
        """The paragraph style a caption of this category wears.

        A fresh document has "Figure", "Table", "Drawing" and "Text" beside
        the "Caption" they all inherit from, so the category's own style is
        preferred and "Caption" is the fallback.
        """
        try:
            styles = doc.StyleFamilies.getByName("ParagraphStyles")
        except Exception as e:
            logger.info(f"Could not reach the paragraph styles: {e}")
            return None
        for name in (category, "Caption"):
            try:
                if styles.hasByName(name):
                    return name
            except Exception as e:
                logger.info(f"Could not ask for the style {name}: {e}")
        return None

    def _resolve_target(self, doc: Any, target: Any) -> tuple:
        """(kind, source, name, sequence_id, description) for a target.

        Raises AddressError for anything this document cannot point at, so a
        reference is never written showing "Error: Reference source not
        found".
        """
        if not isinstance(target, dict) or len(target) != 1:
            raise AddressError(
                "target names exactly one thing: {\"heading\": N}, "
                "{\"caption\": \"Figure 2\"}, {\"bookmark\": \"name\"} or "
                "{\"reference_mark\": \"name\"}")
        kind, value = next(iter(target.items()))

        if kind == "bookmark":
            marks = self._bookmarks(doc)
            if marks is None or not marks.hasByName(value):
                raise AddressError(f"no bookmark called {value!r}; "
                                   f"list_bookmarks says which there are")
            return "bookmark", SOURCE_OF_KIND["bookmark"], value, None, value

        if kind == "reference_mark":
            try:
                names = set(doc.getReferenceMarks().getElementNames())
            except Exception:
                names = set()
            if value not in names:
                raise AddressError(
                    f"no reference mark called {value!r}; "
                    f"list_reference_targets says which there are")
            return ("reference_mark", SOURCE_OF_KIND["reference_mark"], value,
                    None, value)

        if kind == "caption":
            captions = self._captions(doc)
            match = None
            if isinstance(value, dict):
                match = next((one for one in captions
                              if one["category"] == value.get("category")
                              and str(one["number"]) == str(value.get("number"))),
                             None)
            elif isinstance(value, str) and " " in value:
                wanted_category, wanted_number = value.rsplit(" ", 1)
                match = next((one for one in captions
                              if one["category"] == wanted_category
                              and str(one["number"]) == wanted_number), None)
            if match is None:
                raise AddressError(
                    f"no caption {value!r}; list_reference_targets reports "
                    f"every caption as \"Category Number\"")
            return ("caption", SOURCE_OF_KIND["caption"], match["category"],
                    match["sequence_id"], f"{match['category']} "
                                          f"{match['number']}")

        if kind == "heading":
            name = self._heading_bookmark(doc, value)
            return "heading", SOURCE_OF_KIND["bookmark"], name, None, name

        raise AddressError(
            f"a target is a heading, a caption, a bookmark or a "
            f"reference_mark; got {kind!r}")

    def _heading_bookmark(self, doc: Any, paragraph: Any) -> str:
        """The bookmark a reference to a heading points at, made if needed.

        Writer's own dialog leaves a `__RefHeading__…` mark on the heading,
        and a bookmark given that name **becomes one** — measured: it vanishes
        from `getBookmarks()`, so nothing can find it again, and a second one
        on the same heading throws IllegalArgumentException from
        `unobkm.cxx`. So the bookmark made here is an ordinary one, named
        after the heading, which the Navigator shows and `list_bookmarks`
        reports; a bookmark already covering the whole heading is reused, so
        ten references to one heading leave one bookmark.
        """
        from uno_values import _heading_level

        body = doc.getText()
        element = self._paragraph_at(body, paragraph) \
            if isinstance(paragraph, int) and not isinstance(paragraph, bool) \
            and paragraph >= 0 else None
        if element is None or _heading_level(element) <= 0:
            raise AddressError(
                f"paragraph {paragraph!r} is not a heading; put a bookmark on "
                f"ordinary text with add_bookmark and refer to that")

        text = element.getString()
        listed = self._bookmarks_by_paragraph(doc).get(paragraph, [])
        already = self._bookmark_over(listed, text)
        if already:
            return already

        marks = self._bookmarks(doc)
        base = (_text_payload(text)["text"] or "").strip()[:40] \
            or f"Heading {paragraph}"
        name, attempt = base, 1
        while marks is not None and marks.hasByName(name):
            attempt += 1
            name = f"{base} ({attempt})"
        mark = doc.createInstance("com.sun.star.text.Bookmark")
        mark.setName(name)
        span = body.createTextCursorByRange(element.getStart())
        span.gotoEndOfParagraph(True)
        body.insertTextContent(span, mark, True)
        return name

    def insert_cross_reference(self, address: Any, target: Any,
                               part: Optional[str] = None,
                               track_changes: Optional[bool] = None,
                               doc: Any = None) -> Dict[str, Any]:
        """
        Put a reference to a heading, a caption, a bookmark or a reference
        mark where an address points

        The field shows what the target says now and follows it afterwards,
        so a sentence that mentions "Figure 3" stays right when a figure is
        added before it.
        """
        doc, error = self._writer_document(doc, "Inserting a cross-reference")
        if error:
            return error

        try:
            kind, source, name, sequence_id, described = \
                self._resolve_target(doc, target)
        except AddressError as e:
            return refusal("NOT_FOUND" if "no " in str(e) else
                           "INVALID_PARAMETER", e)

        if part is None:
            part = "category_and_number" if kind == "caption" else "text"
        if part == "number" and kind != "caption":
            part = "heading_number"
        if part not in PART_OF_NAME:
            return refusal("INVALID_PARAMETER",
                           f"part is one of {', '.join(sorted(PART_OF_NAME))}, "
                           f"got {part!r}")

        try:
            where = self._resolve_address(doc, address)
        except AddressError as e:
            return refusal("INVALID_ADDRESS", e)

        def edit():
            field = doc.createInstance(REFERENCE_SERVICE)
            field.ReferenceFieldSource = source
            field.SourceName = name
            if sequence_id is not None:
                field.SequenceNumber = sequence_id
            field.ReferenceFieldPart = PART_OF_NAME[part]
            owner = where.getText()
            owner.insertTextContent(where, field, bool(where.getString()))
            doc.getTextFields().refresh()
            located, _, _ = self._locate_range(doc, field.getAnchor())
            return {"target": {"kind": kind, "name": name,
                               "sequence_id": sequence_id,
                               "described": described},
                    "part": part,
                    "shows": field.getPresentation(False),
                    "address": located}

        return self._guarded_edit(doc, "MCP: insert a cross-reference",
                                  track_changes, edit)
