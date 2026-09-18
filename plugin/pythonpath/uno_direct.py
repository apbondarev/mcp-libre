"""Finding by style, and the formatting applied over one.

A document looks inconsistent when direct formatting — bold typed over a
style, a font chosen by hand — sits on top of the styles, and a clean-up is
taking that off without touching what the styles say. Measured on a live
Writer:

  * **a paragraph style can be searched for.** `SearchStyles = True` with the
    style's name as the search string finds every paragraph in it through
    `findAll` — three hits in 0.003s, where walking the body to compare
    `ParaStyleName` is a walk. A **character** style is *not* found this way
    (0 hits), so those are found by walking the portions;
  * direct formatting is `getPropertyState` on the range itself, exactly as
    on a style: DIRECT_VALUE for what was applied over the style,
    DEFAULT_VALUE for what the style decides. `getPropertyStates` answers for
    a whole list in one call, which is what keeps a clean-up cheap;
  * `setPropertyToDefault` takes one property off and
    `setAllPropertiesToDefault` takes the lot — and, measured, **a hyperlink
    survives it** (`HyperLinkURL` and the link's character styles stay) and
    so does a **character style** ("Emphasis" was still there afterwards).
    So clearing direct formatting is not the destructive act it sounds like:
    inline code and links come through. The character style and the language
    are left alone here even so, unless a caller names them: taking the
    language off text would hand it back to the style's, and a Russian
    paragraph would be spell checked as English again;
  * **character formatting applied to a whole paragraph lands on the
    paragraph, not on the text.** Measured: a paragraph made bold end to end
    answers `DEFAULT_VALUE` for `CharWeight` on its range while the value
    reads 150 — the paragraph node carries it — where the same bold on half
    the paragraph answers `DIRECT_VALUE` on that half and
    `AMBIGUOUS_VALUE` on the whole. So both are asked, and both are cleared,
    or "clear the formatting of this paragraph" does nothing at all.
"""

from typing import Any, Dict, List, Optional
import logging

from uno_values import (AddressError, STYLE_FAMILIES, _colour_name,
                        _get_property, _is_italic, _locale_name,
                        _text_payload, refusal)

logger = logging.getLogger(__name__)

# The character properties a clean-up is about, in the words the formatting
# tools use, and the paragraph ones beside them.
DIRECT_CHARACTER = {
    "CharWeight": "bold", "CharPosture": "italic",
    "CharUnderline": "underline", "CharHeight": "font_size",
    "CharFontName": "font_name", "CharColor": "color",
    "CharBackColor": "background_color", "CharStyleName": "character_style",
    "CharLocale": "language", "CharEscapement": "raised_or_lowered",
    "CharStrikeout": "struck_out",
}
# Not swept by a clean-up unless a caller names them: a character style is
# not direct formatting (measured — setAllPropertiesToDefault keeps it), and
# taking a run's language off would leave it spell checked as the style says.
NOT_SWEPT = ("CharStyleName", "CharLocale")

DIRECT_PARAGRAPH = {
    "ParaAdjust": "alignment", "ParaTopMargin": "space_above",
    "ParaBottomMargin": "space_below", "ParaLeftMargin": "indent_left",
    "ParaRightMargin": "indent_right",
    "ParaFirstLineIndent": "first_line_indent",
    "ParaLineSpacing": "line_spacing", "ParaKeepTogether": "keep_together",
    "ParaSplit": "may_split", "FillStyle": "background",
    "FillColor": "background_color", "BreakType": "page_break",
}


def _readable(name: str, value: Any) -> Any:
    """A property value as a caller reads it, not as UNO keeps it"""
    if name == "CharWeight" and isinstance(value, (int, float)):
        return "bold" if value > 120 else "normal"
    if name == "CharPosture":
        return "italic" if _is_italic(value) else "upright"
    if name in ("CharColor", "CharBackColor", "FillColor") \
            and isinstance(value, int):
        return _colour_name(value)
    if name == "CharLocale":
        return _locale_name(value)
    if name in ("ParaTopMargin", "ParaBottomMargin", "ParaLeftMargin",
                "ParaRightMargin", "ParaFirstLineIndent") \
            and isinstance(value, (int, float)):
        return f"{round(value / 100.0, 2)} mm"
    if name == "CharHeight" and isinstance(value, (int, float)):
        return f"{round(float(value), 1)} pt"
    enum = getattr(value, "value", None)
    if isinstance(enum, str):
        return enum
    if hasattr(value, "Mode") and hasattr(value, "Height"):
        return {"mode": value.Mode, "height": value.Height}
    return value


class DirectFormattingMixin:
    """Part of UNOBridge — see uno_bridge.py for how the parts meet."""

    def find_by_style(self, style: str, family: str = "paragraph",
                      address: Any = None, max_results: int = 200,
                      doc: Any = None) -> Dict[str, Any]:
        """
        Every place a style is used — which is how a code block is found

        A paragraph style is searched for rather than walked to: Writer's own
        search takes a style name when `SearchStyles` is on, and answers in
        milliseconds. A character style is not searchable that way, so those
        are found by walking the runs.
        """
        doc, error = self._writer_document(doc, "Finding by style")
        if error:
            return error
        wanted = (family or "paragraph").lower()
        if wanted not in ("paragraph", "character"):
            return refusal("INVALID_PARAMETER",
                           f"family is \"paragraph\" or \"character\", got "
                           f"{family!r}")
        if not self._has_style(doc, STYLE_FAMILIES[wanted], style):
            return refusal("NOT_FOUND",
                           f"this document has no {wanted} style {style!r}; "
                           f"list_styles says which it has")

        try:
            covers, scope = self._comment_scope(doc, address)
        except Exception as e:
            return refusal("INVALID_ADDRESS", e)

        # Placing a hit is a walk shared between them all, and on a real
        # document 184 code blocks cost 1.7s to place — so when nothing is
        # being scoped, only the hits that will be reported are placed.
        limit = None if address is not None else max(1, max_results)
        if wanted == "paragraph":
            hits, total = self._paragraphs_in_style(doc, style, limit)
        else:
            hits = self._runs_in_style(doc, style, address)
            total = len(hits)
        hits = [hit for hit in hits if covers(hit["address"])]
        shown = hits[:max_results]
        more = max(0, total - len(shown))
        return {"success": True, "style": style, "family": wanted,
                "hits": shown, "count": len(shown),
                "not_reported": more or None, "scope": scope}

    def _paragraphs_in_style(self, doc: Any, style: str,
                             limit: Optional[int] = None) -> tuple:
        """(hits, how many there are) for a paragraph style, Writer's own way"""
        found = []
        try:
            descriptor = doc.createSearchDescriptor()
            descriptor.SearchString = style
            descriptor.SearchStyles = True
            matches = doc.findAll(descriptor)
        except Exception as e:
            logger.info(f"Could not search for the style {style}: {e}")
            return found, 0

        total = matches.getCount()
        ranges = [matches.getByIndex(index)
                  for index in range(total if limit is None
                                     else min(total, limit))]
        for match, address in zip(ranges, self._addresses_in_order(doc,
                                                                   ranges)):
            if address is None:
                continue
            found.append({"address": {"paragraph": address.get("paragraph")}
                          if address.get("paragraph") is not None else address,
                          "text": _text_payload(match.getString())["text"]})
        return found, total

    def _runs_in_style(self, doc: Any, style: str, address: Any) -> List[Dict]:
        """The runs wearing a character style, by walking the portions"""
        found = []
        try:
            covers, scope = self._comment_scope(doc, address)
            window = self._window_of(scope)
        except Exception:
            window = None
        first, last = window if window else (0, None)
        index = 0
        try:
            paragraphs = doc.getText().createEnumeration()
        except Exception as e:
            logger.error(f"Could not walk the document: {e}")
            return found
        while paragraphs.hasMoreElements():
            paragraph = paragraphs.nextElement()
            if not hasattr(paragraph, "createEnumeration"):
                continue
            if last is not None and index > last:
                break
            if index < first:
                index += 1
                continue
            offset = 0
            portions = paragraph.createEnumeration()
            while portions.hasMoreElements():
                portion = portions.nextElement()
                body = portion.getString()
                if (_get_property(portion, "CharStyleName", "") or "") == style:
                    found.append({
                        "address": {"paragraph": index, "offset": offset,
                                    "length": len(body)},
                        "text": _text_payload(body)["text"]})
                offset += len(body)
            index += 1
        return found

    # ---- what is applied over the style ------------------------------

    def get_direct_formatting(self, address: Any,
                              doc: Any = None) -> Dict[str, Any]:
        """
        What is formatted by hand over the styles, at an address

        This is what makes a document look inconsistent, and what a clean-up
        takes off. Reported separately for the characters and for the
        paragraph, since the two are cleared separately.
        """
        doc, error = self._writer_document(doc, "Reading direct formatting")
        if error:
            return error
        try:
            target = self._resolve_address(doc, address)
            located, cursor, _ = self._locate_range(
                doc, target, self._paragraph_hint(address, doc))
        except AddressError as e:
            return refusal("INVALID_ADDRESS", e)

        character = self._direct_on(target, DIRECT_CHARACTER)
        paragraph = {}
        index = located.get("paragraph")
        if index is not None:
            element = self._paragraph_at(doc.getText(), index)
            if element is not None:
                paragraph = self._direct_on(element, DIRECT_PARAGRAPH,
                                            where="paragraph")
                # Character formatting applied to a whole paragraph lives on
                # the paragraph, where the range reports nothing — measured.
                for name, entry in self._direct_on(
                        element, DIRECT_CHARACTER,
                        where="paragraph").items():
                    character.setdefault(name, entry)

        return {"success": True,
                "paragraph": index,
                "paragraph_style": _get_property(target, "ParaStyleName", None),
                "character_style": _get_property(target, "CharStyleName", None)
                or None,
                "character": character, "paragraph_formatting": paragraph,
                "count": len(character) + len(paragraph),
                "text": _text_payload(target.getString())["text"]}

    def _direct_on(self, thing: Any, wanted: Dict[str, str],
                   where: str = "text") -> Dict[str, Any]:
        """The properties of `wanted` this thing sets itself, read in bulk.

        `getPropertyStates` answers for the whole list in one call, where
        asking one at a time is a bridge call apiece — measured to matter on
        anything that reads a document a paragraph at a time.
        """
        names = tuple(wanted)
        states = None
        try:
            states = [state.value for state in thing.getPropertyStates(names)]
        except Exception as e:
            logger.info(f"Could not read the property states in one: {e}")
        found = {}
        for position, name in enumerate(names):
            try:
                state = states[position] if states is not None \
                    else thing.getPropertyState(name).value
            except Exception:
                continue
            if state not in ("DIRECT_VALUE", "AMBIGUOUS_VALUE"):
                continue
            try:
                value = getattr(thing, name)
            except Exception:
                continue
            found[wanted[name]] = {"value": _readable(name, value),
                                   "property": name,
                                   # A range whose parts differ answers
                                   # AMBIGUOUS_VALUE and hands back no value
                                   # at all — worth saying, since a caller
                                   # cannot read one number off it.
                                   "mixed": state == "AMBIGUOUS_VALUE",
                                   "where": where}
        return found

    def clear_direct_formatting(self, address: Any, characters: bool = True,
                                paragraphs: bool = False,
                                properties: Optional[List[str]] = None,
                                track_changes: Optional[bool] = None,
                                doc: Any = None) -> Dict[str, Any]:
        """
        Take the formatting applied over the styles off again

        Measured, and worth knowing before running it over a document: a
        hyperlink **survives** this, and so does a character style — "Source
        Text" on inline code and the link's own look are not direct
        formatting. What goes is the bold, the fonts and the colours somebody
        applied by hand.
        """
        doc, error = self._writer_document(doc, "Clearing direct formatting")
        if error:
            return error
        if not characters and not paragraphs:
            return refusal("INVALID_PARAMETER",
                           "say what to clear: characters, paragraphs, or "
                           "both")
        by_name = {**{value: key for key, value in DIRECT_CHARACTER.items()},
                   **{value: key for key, value in DIRECT_PARAGRAPH.items()}}
        chosen = None
        if properties is not None:
            if not isinstance(properties, (list, tuple)) or not properties:
                return refusal("INVALID_PARAMETER",
                               "properties is a list of names, such as "
                               "[\"bold\", \"color\"]")
            unknown = [one for one in properties if one not in by_name]
            if unknown:
                return refusal("INVALID_PARAMETER",
                               f"nothing here is called {unknown[0]!r}; the "
                               f"names are those get_direct_formatting "
                               f"reports")
            chosen = {by_name[one] for one in properties}

        try:
            target = self._resolve_address(doc, address)
            located, _, _ = self._locate_range(
                doc, target, self._paragraph_hint(address, doc))
        except AddressError as e:
            return refusal("INVALID_ADDRESS", e)
        protected = self._refuse_protected(doc, target, False)
        if protected:
            return protected

        was = self.get_direct_formatting(address, doc=doc)

        before = self._carried(doc, located)

        def edit():
            cleared = {"characters": [], "paragraphs": []}
            if characters:
                cleared["characters"] = self._clear_on(
                    target, DIRECT_CHARACTER, chosen)
                # And on the paragraph, which is where formatting applied to
                # a whole paragraph actually sits.
                if located.get("paragraph") is not None:
                    element = self._paragraph_at(doc.getText(),
                                                 located["paragraph"])
                    if element is not None:
                        for one in self._clear_on(element, DIRECT_CHARACTER,
                                                  chosen):
                            if one not in cleared["characters"]:
                                cleared["characters"].append(one)
            if paragraphs and located.get("paragraph") is not None:
                block = self._block_of(address, located)
                first, last = block if block else (located["paragraph"],
                                                   located["paragraph"])
                for paragraph, index in self._body_paragraphs(doc):
                    if index < first:
                        continue
                    if index > last:
                        break
                    cleared["paragraphs"].extend(
                        self._clear_on(paragraph, DIRECT_PARAGRAPH, chosen))
            return {"cleared": cleared,
                    "character_properties": len(cleared["characters"]),
                    "paragraph_properties": len(cleared["paragraphs"]),
                    "was": {"character": was.get("character"),
                            "paragraph_formatting":
                                was.get("paragraph_formatting")},
                    # Measured: neither a link nor a character style is
                    # direct formatting, so neither goes — counted rather
                    # than claimed, since a mixed range answers nothing
                    # useful when asked for one property.
                    **self._what_survived(doc, located, before)}

        return self._guarded_edit(doc, "MCP: clear direct formatting",
                                  track_changes, edit)

    def _carried(self, doc: Any, located: Dict[str, Any]) -> tuple:
        """(links, character styles) on the runs of a range, counted"""
        try:
            cursor = self._resolve_address(doc, located)
            runs = self._runs_in(doc, located, cursor)
        except Exception as e:
            logger.info(f"Could not count what the runs carry: {e}")
            return None
        return (sum(1 for run in runs if run.get("link")),
                sum(1 for run in runs if run.get("character_style")))

    def _what_survived(self, doc: Any, located: Dict[str, Any],
                       before: Any) -> Dict[str, Any]:
        after = self._carried(doc, located)
        if before is None or after is None:
            return {}
        return {"links_kept": after[0], "links_before": before[0],
                "character_styles_kept": after[1],
                "character_styles_before": before[1]}

    def _clear_on(self, thing: Any, wanted: Dict[str, str],
                  chosen: Optional[set]) -> List[str]:
        """Set back to default whatever of `wanted` was applied by hand"""
        cleared = []
        for name, friendly in wanted.items():
            if chosen is not None and name not in chosen:
                continue
            if chosen is None and name in NOT_SWEPT:
                continue
            try:
                if thing.getPropertyState(name).value != "DIRECT_VALUE":
                    continue
                thing.setPropertyToDefault(name)
                cleared.append(friendly)
            except Exception as e:
                logger.info(f"Could not clear {name}: {e}")
        return cleared
