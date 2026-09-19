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

    def _math_of(self, obj: Any) -> Any:
        """The Math model of an embedded object, or None if it is not one."""
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
            try:
                anchors.append(obj.getAnchor())
                held.append((name, model.Formula))
            except Exception as e:
                logger.info(f"Could not place formula {name}: {e}")
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
        # sentence is said by what is on either side of it.
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
        return described

    # ---- reading ------------------------------------------------------

    def list_formulas(self, address: Any = None,
                      doc: Any = None) -> Dict[str, Any]:
        """
        The formulas of a document, in reading order, each with its text

        Scoped like the comments and the bookmarks. Embedded objects that are
        not formulas — a chart, a drawing — are not listed.
        """
        doc, error = self._writer_document(doc, "Listing formulas")
        if error:
            return error
        formulas = self._formulas_of(doc)
        if formulas is None:
            return refusal("UNSUPPORTED", "this document keeps no embedded objects")

        try:
            covers, scope = self._comment_scope(doc, address)
        except Exception as e:
            return refusal("INVALID_ADDRESS", e)

        names, held, anchors = [], [], []
        for name, obj, model in formulas:
            try:
                anchors.append(obj.getAnchor())
            except Exception as e:
                logger.info(f"Could not anchor formula {name}: {e}")
                continue
            names.append(name)
            held.append((obj, model))
        placed = self._addresses_in_order(doc, anchors)

        found = []
        for name, (obj, model), located in zip(names, held, placed):
            described = self._describe_formula(doc, name, obj, model,
                                               address=located)
            if not covers(described["address"]):
                continue
            found.append(described)

        # In a table cell a formula has no body paragraph, so it sorts after
        # the ones that do, by table and cell.
        def where(one):
            place = one["address"] or {}
            paragraph = place.get("paragraph")
            return (10 ** 9 if paragraph is None else paragraph,
                    place.get("table") or "", place.get("cell") or "",
                    place.get("offset") or 0)

        found.sort(key=where)
        return {"success": True, "formulas": found, "count": len(found),
                "scope": scope}

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
