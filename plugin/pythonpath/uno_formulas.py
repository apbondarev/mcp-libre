"""Formulas: the maths a document carries as objects of their own.

A formula in Writer is not text. It is an embedded LibreOffice Math document
(`com.sun.star.text.TextEmbeddedObject`, class id `078B7ABA-…`) sitting in the
paragraph as one inline object, and every tool that reads a paragraph's string
walks straight past it: "the volume is  of the whole" was all an assistant
could see of "the volume is 1/5 of the whole". Measured on a real Writer:

  * the formula's text — StarMath, `{ frac { 1 } { 5 } }` — is the `Formula`
    property of the object's `Model`; reading and writing it is all there is;
  * an inline formula adds **no character** to the paragraph's string, and its
    anchor is an empty range at the spot, so its place is a paragraph and an
    offset like a caret's, and its neighbours are read off the paragraph;
  * `getEmbeddedObjects()` holds every embedded object, charts and drawings
    included, and answers in no particular order (Object3, Object2, Object1
    for a document written in that order), so the ones that carry a formula
    are picked out by that property and put in reading order here;
  * a new one is named by Writer (`Object1`, localised), and an object given a
    name that is taken **is refused** — no "Copy 1" as with a bookmark;
  * `removeTextContent` takes the object and leaves the text around it;
  * **the object does not resize itself**: a new one is 35.3 x 4.7 mm and stays
    that after `Formula` is set, however much or little the formula needs, so
    a fraction is squashed and "a = b" is stretched — inserted formulas looked
    badly distorted until the object was given the size the Math document says
    it takes (`getVisualAreaSize`, which answers in the unit `getMapUnit`
    names: twips, 9, for a formula);
  * changing `Formula` writes into the formula's own document, so it leaves
    **no step on the text document's Undo list** — an undo context around it
    stays empty and makes no entry. `set_formula` reports `was` instead.
"""

from typing import Any, Dict, List, Optional, Tuple
import logging

import uno

from uno_values import (AddressError, _anchor_kind, _get_property,
                        _millimetres, _text_payload, refusal)

logger = logging.getLogger(__name__)

MATH_CLSID = "078B7ABA-54FC-457F-8551-6147e776a997"
EMBEDDED_OBJECT_SERVICE = "com.sun.star.text.TextEmbeddedObject"

# tools' MapUnit, as hundredths of a millimetre per unit. A Math document
# reports its size in twips (9).
HUNDREDTHS_OF_MM = {0: 1, 1: 10, 2: 100, 3: 1000, 4: 2.54, 5: 25.4, 6: 254,
                    7: 2540, 8: 2540 / 72, 9: 2540 / 1440}
ASPECT_CONTENT = 1

# How a formula shows where it stands when a paragraph is read as text. Not
# braces or brackets: StarMath uses both, and a marker that could be mistaken
# for part of the formula would be worse than none.
FORMULA_MARK = "⟦formula: {}⟧"

# How much of the paragraph either side of a formula is shown, so a reader can
# tell which "equals ___ of the whole" a formula belongs to.
CONTEXT = 40


class FormulasMixin:
    """Part of UNOBridge — see uno_bridge.py for how the parts meet."""

    def _is_math(self, obj: Any) -> bool:
        """Whether an embedded object is a formula, asked the cheap way.

        `Model` **loads the object**, and a real guide holds hundreds of
        them: asking every one for its model cost 2.8s to find a single
        formula, and every `read_paragraphs` call paid it — 4.3s to read one
        paragraph. `CLSID` is a plain property and says the same thing.
        """
        clsid = (_get_property(obj, "CLSID", "") or "").strip("{}").lower()
        return clsid == MATH_CLSID.lower()

    def _math_of(self, obj: Any) -> Any:
        """The Math model of an embedded object, or None if it is not one."""
        if not self._is_math(obj):
            return None
        try:
            model = obj.Model
            text = model.Formula
        except Exception:
            return None
        return model if isinstance(text, str) else None

    def _formula_places(self, doc: Any) -> List[Dict[str, Any]]:
        """Where the formulas stand in the body: [{name, formula, paragraph,
        offset}] in reading order, one sweep of the document, [] when there
        are none. What the reading tools use to say what a paragraph's string
        leaves out.
        """
        formulas = self._formulas_of(doc)
        if not formulas:
            return []
        held, anchors = [], []
        for name, obj, model in formulas:
            # Both or neither: an anchor kept while its formula was not left
            # the two lists of different lengths, and the zip below then gave
            # every formula after it another one's place.
            try:
                anchor, text = obj.getAnchor(), model.Formula
            except Exception as e:
                logger.info(f"Could not place formula {name}: {e}")
                continue
            anchors.append(anchor)
            held.append((name, text))
        placed = self._addresses_in_order(doc, anchors)
        places = []
        for (name, text), located in zip(held, placed):
            if not located or not isinstance(located.get("paragraph"), int):
                continue                    # in a table cell: no body paragraph
            places.append({"name": name, "formula": text,
                           "paragraph": located["paragraph"],
                           "offset": located.get("offset", 0)})
        places.sort(key=lambda one: (one["paragraph"], one["offset"]))
        return places

    def _formulas_in(self, doc: Any, paragraph: int, start: int, end: int,
                     paragraph_cursor: Any) -> List[Dict[str, Any]]:
        """The formulas standing in a stretch of one paragraph

        Placing every formula of a document addresses every anchor, and that
        walks the body — a cost no tool may pay to answer about one
        paragraph, which is what `read_runs` asks. Each anchor is compared
        with this paragraph first, three UNO calls apiece, exactly as
        `_images_in` compares a picture's; the few that fall inside are then
        measured against the paragraph they are already known to be in, so
        `_locate_range` has its index and skips the walk.
        """
        if paragraph_cursor is None:
            return [one for one in self._formula_places(doc)
                    if one["paragraph"] == paragraph
                    and start <= one["offset"] <= end]
        found = []
        for name, obj, model in (self._formulas_of(doc) or []):
            if not self._anchored_in(doc, obj, paragraph_cursor):
                continue
            try:
                # With no paragraph number — a read through an anchor — the
                # offset is still measured, and finding the number is the
                # walk this whole path exists to avoid.
                address, _, _ = self._locate_range(
                    doc, obj.getAnchor(), paragraph,
                    find_paragraph=paragraph is not None)
                text = model.Formula
            except Exception as e:
                logger.info(f"Could not place formula {name}: {e}")
                continue
            offset = address.get("offset")
            if offset is None or not (start <= offset <= end):
                continue
            found.append({"name": name, "formula": text,
                          "paragraph": paragraph, "offset": offset})
        found.sort(key=lambda one: one["offset"])
        return found

    def _formulas_in_paragraph(self, paragraph: Any) -> List[Dict[str, Any]]:
        """The formulas standing in this paragraph, from its own portions.

        Measured on a live Writer: a formula is a portion of type `Frame`
        whose content enumeration hands out the embedded object — the same
        shape a picture has. So a paragraph names its own formulas, and the
        document's embedded objects need not be scanned at all: that scan is
        a fixed cost per call, and it made reading **one** paragraph of a
        real guide dearer than reading fifty.
        """
        found = []
        for name, obj, model, offset in self._formula_objects_in(paragraph):
            try:
                found.append({"name": name, "formula": model.Formula,
                              "offset": offset})
            except Exception as e:
                logger.info(f"A formula would not answer: {e}")
        return found

    def _formula_objects_in(self, paragraph: Any) -> List[tuple]:
        """(name, object, Math model, offset) for the formulas of a paragraph

        The one walk both the readers and `list_formulas` stand on: a formula
        hangs off an empty portion of type `Frame`, and its content
        enumeration hands out the object itself.
        """
        found = []
        offset = 0
        try:
            portions = paragraph.createEnumeration()
        except Exception as e:
            logger.info(f"A paragraph would not say what is in it: {e}")
            return found
        while portions.hasMoreElements():
            portion = portions.nextElement()
            kind = _get_property(portion, "TextPortionType", "Text")
            if kind == "Frame":
                for held in self._contents_of(portion):
                    model = self._math_of(held)
                    if model is None:
                        continue
                    try:
                        found.append((held.getName(), held, model, offset))
                    except Exception as e:
                        logger.info(f"A formula would not name itself: {e}")
                continue
            try:
                offset += len(portion.getString())
            except Exception:
                continue
        return found

    def _formulas_here(self, doc: Any, formulas: List[Tuple],
                       paragraph: Any) -> List[Dict[str, Any]]:
        """The formulas standing in this paragraph, compared not placed.

        Four UNO calls per formula against the walk of the body that placing
        them all costs — and a paragraph holds none of them nearly always.
        """
        found = []
        for name, obj, model in formulas:
            try:
                anchor = obj.getAnchor()
                if not self._covers(doc.getText(), paragraph, anchor):
                    continue
                address, _, _ = self._locate_range(doc, anchor,
                                                   find_paragraph=False)
                found.append({"name": name, "formula": model.Formula,
                              "offset": address.get("offset", 0)})
            except Exception as e:
                logger.info(f"Could not place formula {name}: {e}")
        found.sort(key=lambda one: one["offset"])
        return found

    def _formulas_across(self, doc: Any, first: int, last: int,
                         span: Any = None) -> List[Dict[str, Any]]:
        """The formulas standing in a block of paragraphs

        Placing every formula of a document addresses every anchor, which
        walks the body — 4.0s on a real guide holding one formula, paid by a
        `read_runs` that asked about ten paragraphs nowhere near it. One
        comparison per formula says whether it is in the block at all, and
        only the ones that are get placed.
        """
        formulas = self._formulas_of(doc) or []
        if not formulas:
            return []
        if span is None:
            return [one for one in self._formula_places(doc)
                    if first <= one["paragraph"] <= last]
        found = []
        for name, obj, model in formulas:
            if not self._anchored_in(doc, obj, span):
                continue
            try:
                address, _, _ = self._locate_range(doc, obj.getAnchor())
                text = model.Formula
            except Exception as e:
                logger.info(f"Could not place formula {name}: {e}")
                continue
            paragraph = address.get("paragraph")
            if paragraph is None or not first <= paragraph <= last:
                continue
            found.append({"name": name, "formula": text,
                          "paragraph": paragraph,
                          "offset": address.get("offset", 0)})
        found.sort(key=lambda one: (one["paragraph"], one["offset"]))
        return found

    @staticmethod
    def _with_formulas(text: str, places: List[Dict[str, Any]]) -> str:
        """A paragraph's string with each formula put back where it stands."""
        for one in sorted(places, key=lambda one: -one["offset"]):
            at = one["offset"]
            text = text[:at] + FORMULA_MARK.format(one["formula"]) + text[at:]
        return text

    def _fit_to_formula(self, obj: Any) -> bool:
        """Give the object the size its formula takes.

        Writer leaves it at the 35.3 x 4.7 mm it was made with, which distorts
        every formula that is not exactly that shape. The Math document knows
        how big it wants to be.
        """
        try:
            shown = obj.getEmbeddedObject()
            size = shown.getVisualAreaSize(ASPECT_CONTENT)
            scale = HUNDREDTHS_OF_MM[shown.getMapUnit(ASPECT_CONTENT)]
            width, height = round(size.Width * scale), round(size.Height * scale)
        except Exception as e:
            logger.info(f"Could not size a formula: {e}")
            return False
        if width <= 0 or height <= 0:
            return False
        obj.Width, obj.Height = width, height
        return True

    def _formulas_of(self, doc: Any) -> Optional[List[Tuple[str, Any, Any]]]:
        """[(name, object, model)] for the formulas of a document, or None."""
        try:
            objects = doc.getEmbeddedObjects()
            names = list(objects.getElementNames())
        except Exception as e:
            logger.error(f"Could not reach the embedded objects: {e}")
            return None
        found = []
        for name in names:
            try:
                obj = objects.getByName(name)
            except Exception as e:
                logger.info(f"Could not read embedded object {name}: {e}")
                continue
            model = self._math_of(obj)
            if model is not None:
                found.append((name, obj, model))
        return found

    def _formula_named(self, doc: Any, name: Any) -> Tuple[Any, Any]:
        """(object, model) for a formula by name, or (refusal, None)."""
        formulas = self._formulas_of(doc)
        if formulas is None:
            return refusal("UNSUPPORTED",
                           "this document keeps no embedded objects"), None
        for found, obj, model in formulas:
            if found == name:
                return obj, model
        return refusal("NOT_FOUND",
                       f"no formula called {name!r}; list_formulas says which "
                       f"there are"), None

    def _describe_formula(self, doc: Any, name: str, obj: Any, model: Any,
                          address: Any = "unplaced") -> Dict[str, Any]:
        """A formula as a caller sees it: its text, its place, its neighbours.

        `address` is taken from the caller when a sweep has already placed it,
        as `list_bookmarks` does — placing one anchor walks the body.
        """
        described: Dict[str, Any] = {"name": name}
        try:
            described["formula"] = model.Formula
        except Exception as e:
            logger.info(f"Could not read formula {name}: {e}")
            described["formula"] = None
        try:
            if address == "unplaced":
                address, _, _ = self._locate_range(doc, obj.getAnchor())
        except Exception as e:
            logger.info(f"Could not place formula {name}: {e}")
            address = None
        described["address"] = address
        described["inline"] = _anchor_kind(
            _get_property(obj, "AnchorType", None)) == "AS_CHARACTER"
        described["width_mm"] = _millimetres(_get_property(obj, "Width", None))
        described["height_mm"] = _millimetres(_get_property(obj, "Height", None))

        # The paragraph does not hold the formula, so where it stands in the
        # sentence is said by what is on either side of it — read from the
        # anchor, which knows its own paragraph, rather than from a number,
        # which would have to be counted to.
        paragraph = (address or {}).get("paragraph")
        offset = (address or {}).get("offset")
        if isinstance(paragraph, int) and isinstance(offset, int):
            try:
                text = self._paragraph_at(doc.getText(), paragraph).getString()
                described["text_before"] = _text_payload(
                    text[max(0, offset - CONTEXT):offset])["text"]
                described["text_after"] = _text_payload(
                    text[offset:offset + CONTEXT])["text"]
            except Exception as e:
                logger.info(f"Could not read around formula {name}: {e}")
        else:
            before, after = self._words_around(obj)
            if before is not None:
                described["text_before"] = before
            if after is not None:
                described["text_after"] = after
        return described

    def _words_around(self, obj: Any) -> tuple:
        """What stands on either side of a formula, from its anchor alone

        Six UNO calls and no walk: the anchor is an empty range at the spot,
        so a cursor from the start of its paragraph to it is the text before,
        and one from it to the end is the text after.
        """
        try:
            anchor = obj.getAnchor()
            owner = anchor.getText()
            before = owner.createTextCursorByRange(anchor)
            before.gotoStartOfParagraph(True)
            after = owner.createTextCursorByRange(anchor)
            after.gotoEndOfParagraph(True)
        except Exception as e:
            logger.info(f"Could not read around a formula: {e}")
            return None, None
        try:
            return (_text_payload(before.getString()[-CONTEXT:])["text"],
                    _text_payload(after.getString()[:CONTEXT])["text"])
        except Exception as e:
            logger.info(f"Could not read around a formula: {e}")
            return None, None

    # ---- reading ------------------------------------------------------

    def list_formulas(self, address: Any = None, number: bool = False,
                      doc: Any = None) -> Dict[str, Any]:
        """
        The formulas of a document, each with its text and where it stands

        Scoped like the comments and the bookmarks. Embedded objects that are
        not formulas — a chart, a drawing — are not listed.

        **Finding them was never the cost.** Measured on a 519-page guide
        holding exactly one formula: asking the document for its embedded
        objects is 0.001s and picking the formula out of them 0.006s, while
        the call took **5.14s** — all of it spent placing that one formula by
        paragraph number, which is a sweep of the body, plus another walk when
        the scope had to be numbered too (7.1s through an anchor). Each
        formula now carries an anchor, a scope compares ranges, and a scoped
        call reads the `Frame` portions of its own paragraphs, where the
        object hangs. `number: true` buys the sweep and reading order.
        """
        doc, error = self._writer_document(doc, "Listing formulas")
        if error:
            return error
        if self._formulas_of(doc) is None:
            return refusal("UNSUPPORTED", "this document keeps no embedded objects")

        try:
            covers, scope = (self._comment_scope(doc, address) if number
                             else self._scope_over(doc, address))
        except Exception as e:
            return refusal("INVALID_ADDRESS", e)

        if address is None or number:
            formulas = self._formulas_of(doc) or []
        else:
            formulas = self._formulas_over(doc, address)

        names, held, anchors = [], [], []
        for name, obj, model in formulas:
            try:
                anchors.append(obj.getAnchor())
            except Exception as e:
                logger.info(f"Could not anchor formula {name}: {e}")
                continue
            names.append(name)
            held.append((obj, model))
        if not number and len(anchors) > 1:
            # `getEmbeddedObjects()` names them in no order at all (Object3,
            # Object2, Object1 — measured), and reading order is what a
            # caller means by a list of formulas. Comparing the anchors is
            # one UNO call per comparison, a handful for a handful of
            # formulas, where numbering them is a sweep of the whole body.
            order = self._by_where_they_stand(doc, anchors)
            names = [names[at] for at in order]
            held = [held[at] for at in order]
            anchors = [anchors[at] for at in order]
        placed = (self._addresses_in_order(doc, anchors) if number
                  else [None] * len(anchors))

        found = []
        for name, (obj, model), located, anchor in zip(names, held, placed,
                                                       anchors):
            if number:
                if not covers(located):
                    continue
            else:
                if not covers(anchor):
                    continue
                located = {"anchor": self._anchor_handle(
                    self._hold_anchor(doc, anchor), "text")}
            found.append(self._describe_formula(doc, name, obj, model,
                                                address=located))

        # In a table cell a formula has no body paragraph, so it sorts after
        # the ones that do, by table and cell.
        def where(one):
            place = one["address"] or {}
            paragraph = place.get("paragraph")
            return (10 ** 9 if paragraph is None else paragraph,
                    place.get("table") or "", place.get("cell") or "",
                    place.get("offset") or 0)

        if number:
            found.sort(key=where)
        return {"success": True, "formulas": found, "count": len(found),
                "order": "reading", "scope": scope}

    def _by_where_they_stand(self, doc: Any, anchors: List[Any]) -> List[int]:
        """The indices of `anchors`, in reading order, by comparing them

        A range inside a table cell cannot be compared with one in the body —
        it throws — so those keep their place rather than failing the sort.
        """
        import functools
        body = doc.getText()

        def compare(one: int, other: int) -> int:
            try:
                return -body.compareRegionStarts(anchors[one], anchors[other])
            except Exception:
                return 0

        return sorted(range(len(anchors)), key=functools.cmp_to_key(compare))

    def _formulas_over(self, doc: Any, address: Any) -> List[tuple]:
        """The formulas in the paragraphs a scope covers, from their portions

        A formula is an empty portion of type `Frame` whose content
        enumeration hands out the embedded object — the same shape a picture
        has — so a paragraph names its own, and the scope costs the scope.
        """
        paragraphs, _, _ = self._paragraphs_over(doc, address)
        if paragraphs is None:
            return self._formulas_of(doc) or []
        found, seen = [], set()
        for paragraph in paragraphs:
            for name, obj, model, _offset in \
                    self._formula_objects_in(paragraph):
                if name in seen:
                    continue
                seen.add(name)
                found.append((name, obj, model))
        return found

    # ---- making, changing, taking away --------------------------------

    def add_formula(self, address: Any, formula: str,
                    name: Optional[str] = None, replace_text: bool = False,
                    track_changes: Optional[bool] = None,
                    doc: Any = None) -> Dict[str, Any]:
        """
        Put a formula in the text, as an inline object

        It goes at the start of the range the address names, and takes nothing
        with it unless `replace_text` says the covered text is what it stands
        in for.
        """
        doc, error = self._writer_document(doc, "Adding a formula")
        if error:
            return error
        if not isinstance(formula, str) or not formula.strip():
            return refusal("INVALID_PARAMETER",
                           "a formula needs its text, in StarMath: "
                           "\"{ frac { 1 } { 5 } }\"")
        formula = formula.strip()
        if name is not None:
            if not isinstance(name, str) or not name.strip():
                return refusal("INVALID_PARAMETER",
                               "a formula's name must be a non-empty string, "
                               "or left out for Writer to choose one")
            name = name.strip()
            taken = self._formulas_of(doc) or []
            if any(found == name for found, _, _ in taken):
                return refusal("INVALID_PARAMETER",
                               f"this document already has a formula called "
                               f"{name!r}")

        try:
            target = self._resolve_address(doc, address)
        except AddressError as e:
            return refusal("INVALID_ADDRESS", e)
        protected = self._refuse_protected(doc, target, False)
        if protected:
            return protected

        def edit():
            obj = doc.createInstance(EMBEDDED_OBJECT_SERVICE)
            obj.CLSID = MATH_CLSID
            obj.AnchorType = uno.Enum("com.sun.star.text.TextContentAnchorType",
                                      "AS_CHARACTER")
            where = target if replace_text else target.getStart()
            target.getText().insertTextContent(where, obj, bool(replace_text))
            model = self._math_of(obj)
            if model is None:
                raise RuntimeError("Writer made no formula object")
            model.Formula = formula
            self._fit_to_formula(obj)
            if name:
                obj.setName(name)
            return self._describe_formula(doc, obj.getName(), obj, model)

        return self._guarded_edit(doc, "MCP: add a formula", track_changes,
                                  edit)

    def set_formula(self, name: str, formula: str,
                    doc: Any = None) -> Dict[str, Any]:
        """Change what a formula says, leaving it where it is"""
        doc, error = self._writer_document(doc, "Changing a formula")
        if error:
            return error
        if not isinstance(formula, str) or not formula.strip():
            return refusal("INVALID_PARAMETER",
                           "a formula needs its text, in StarMath")
        obj, model = self._formula_named(doc, name)
        if model is None:
            return obj
        protected = self._refuse_protected(doc, obj.getAnchor(), False)
        if protected:
            return protected

        formula = formula.strip()
        was = model.Formula

        def edit():
            model.Formula = formula
            self._fit_to_formula(obj)
            described = self._describe_formula(doc, name, obj, model)
            described["was"] = was
            return described

        return self._guarded_edit(doc, "MCP: change a formula", None, edit)

    def delete_formula(self, name: str, doc: Any = None) -> Dict[str, Any]:
        """Take a formula away, leaving the text around it"""
        doc, error = self._writer_document(doc, "Deleting a formula")
        if error:
            return error
        obj, model = self._formula_named(doc, name)
        if model is None:
            return obj
        protected = self._refuse_protected(doc, obj.getAnchor(), False)
        if protected:
            return protected
        described = self._describe_formula(doc, name, obj, model)

        def edit():
            obj.getAnchor().getText().removeTextContent(obj)
            return {"deleted": name, "was": described["formula"],
                    "address": described["address"],
                    "text_before": described.get("text_before"),
                    "text_after": described.get("text_after")}

        return self._guarded_edit(doc, "MCP: delete a formula", None, edit)
