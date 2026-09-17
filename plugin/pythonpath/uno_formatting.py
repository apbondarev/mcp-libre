"""Formatting, and the styles behind it.

A paragraph's background needs FillStyle on the paragraph object, since
ParaBackColor is silently unwritable; borders are TopBorder and its
neighbours, not ParaTopBorder. A style's own definition is what UNO reports
as DIRECT_VALUE, which is how describe_style tells it from what is inherited.
"""

import uno
from typing import Any, Optional, Dict
import logging
from uno_values import (AddressError, STYLE_EFFECTIVE, STYLE_FAMILIES, 
    UNVISITED_LINK_STYLE, VISITED_LINK_STYLE, WRITER_SERVICE, _border_line, 
    _colour, _colour_name, _get_property, _get_property_state, 
    _points_to_uno, _raw, _style_value, _supports, refusal)

logger = logging.getLogger(__name__)


class FormattingMixin:
    """Part of UNOBridge — see uno_bridge.py for how the parts meet."""

    def format_text(self, formatting: Dict[str, Any], doc: Any = None) -> Dict[str, Any]:
        """
        Apply formatting to selected text
        
        Args:
            formatting: Dictionary of formatting options
            doc: Document to format (None for active document)
            
        Returns:
            Result dictionary
        """
        try:
            if doc is None:
                doc = self.get_active_document()
            
            if not doc or not _supports(doc, WRITER_SERVICE):
                return {"success": False, "code": "NO_DOCUMENT", "error": "No Writer document available"}
            
            # The selection, which must actually hold something: a collapsed
            # caret answers getCount() == 1 with an empty range, so the old
            # check never fired and this reported success while doing nothing.
            try:
                text_range = self._resolve_address(doc, {"selection": True})
            except AddressError as e:
                return refusal("INVALID_ADDRESS", e)

            if not text_range.getString():
                return {
                    "success": False,
                    "code": "INVALID_ADDRESS",
                    "error": "Nothing is selected, so there is nothing to "
                             "format. Select the text first, or use "
                             "format_range with an address."
                }
            
            # Apply various formatting options
            if "bold" in formatting:
                text_range.CharWeight = 150.0 if formatting["bold"] else 100.0
            
            if "italic" in formatting:
                text_range.CharPosture = 2 if formatting["italic"] else 0
            
            if "underline" in formatting:
                text_range.CharUnderline = 1 if formatting["underline"] else 0
            
            if "font_size" in formatting:
                text_range.CharHeight = formatting["font_size"]
            
            if "font_name" in formatting:
                text_range.CharFontName = formatting["font_name"]
            
            logger.info("Applied formatting to selected text")
            return {"success": True, "message": "Formatting applied successfully"}
            
        except Exception as e:
            logger.error(f"Failed to format text: {e}")
            return refusal("FAILED", e)

    def format_ranges(self, ranges: Any, track_changes: Optional[bool] = None,
                      doc: Any = None) -> Dict[str, Any]:
        """
        Apply character formatting to many places in one edit

        Syntax colouring is one span per token, and doing that a call at a
        time is what sends an assistant off to write its own script: a code
        block of twenty tokens is twenty calls and twenty undo steps. Here
        they are one call and one undo step, and every address is checked
        before a single one is written, so a mistake in the tenth span does
        not leave the first nine applied.

        Character formatting changes no text, so the addresses stay true as
        the edit goes along.
        """
        doc, error = self._writer_document(doc, "Formatting text")
        if error:
            return error

        if not isinstance(ranges, (list, tuple)) or not ranges:
            return {"success": False, "code": "INVALID_PARAMETER",
                    "error": 'ranges must be a list, each entry an address '
                             'with the formatting for it, as in '
                             '[{"address": {"paragraph": 3, "offset": 0, '
                             '"length": 5}, "color": "#0B7285"}]'}

        prepared = []
        for position, entry in enumerate(ranges):
            if not isinstance(entry, dict):
                return {"success": False, "code": "INVALID_PARAMETER",
                        "error": f"range {position} must be an object with an "
                                 f"address and the formatting for it"}
            address = entry.get("address")
            if address is None:
                return {"success": False, "code": "INVALID_PARAMETER",
                        "error": f"range {position} has no address"}

            asked = {}
            for key in ("bold", "italic", "underline"):
                if entry.get(key) is not None:
                    asked[key] = bool(entry[key])
            if entry.get("font_size") is not None:
                asked["font_size"] = float(entry["font_size"])
            if entry.get("font_name") is not None:
                asked["font_name"] = str(entry["font_name"])
            try:
                for key in ("color", "background_color"):
                    if entry.get(key) is not None:
                        asked[key] = _colour_name(_colour(entry[key]))
                target = self._resolve_address(doc, address)
            except AddressError as e:
                return {"success": False, "code": "INVALID_ADDRESS",
                        "error": f"range {position}: {e}"}
            if not asked:
                return {"success": False, "code": "INVALID_PARAMETER",
                        "error": f"range {position} asks for no formatting"}
            prepared.append((target, asked, address))

        def edit():
            for target, asked, _address in prepared:
                self._apply_character_formatting(target, asked)
            return {"ranges": len(prepared),
                    "characters": sum(len(target.getString())
                                      for target, _asked, _address in prepared)}

        return self._guarded_edit(doc, "MCP: format ranges", track_changes,
                                  edit)

    def format_range(self, address: Any, bold: Optional[bool] = None,
                     italic: Optional[bool] = None,
                     underline: Optional[bool] = None,
                     font_size: Optional[float] = None,
                     font_name: Optional[str] = None,
                     color: Any = None,
                     background_color: Any = None,
                     track_changes: Optional[bool] = None,
                     doc: Any = None) -> Dict[str, Any]:
        """
        Apply character formatting to the text at an address

        format_text can only reach the human's current selection, and nothing
        in this server can select, so an assistant asked to make a code block
        monospace had no way to do it. This takes an address instead.
        """
        doc, error = self._writer_document(doc, "Formatting text")
        if error:
            return error

        asked = {}
        if bold is not None:
            asked["bold"] = bool(bold)
        if italic is not None:
            asked["italic"] = bool(italic)
        if underline is not None:
            asked["underline"] = bool(underline)
        if font_size is not None:
            asked["font_size"] = float(font_size)
        if font_name is not None:
            asked["font_name"] = str(font_name)
        try:
            if color is not None:
                asked["color"] = _colour_name(_colour(color))
            if background_color is not None:
                asked["background_color"] = _colour_name(_colour(background_color))
        except AddressError as e:
            return refusal("INVALID_ADDRESS", e)

        if not asked:
            return {"success": False, "code": "INVALID_PARAMETER",
                    "error": "Nothing to apply: pass at least one of bold, "
                             "italic, underline, font_size, font_name, "
                             "color, background_color"}

        def edit():
            target = self._resolve_address(doc, address)
            self._apply_character_formatting(target, asked)
            return {"applied": asked, "characters": len(target.getString())}

        return self._guarded_edit(doc, "MCP: format text", track_changes, edit)

    def _apply_character_formatting(self, target: Any, asked: Dict[str, Any]):
        """Write the character properties a formatting request asked for"""
        if "bold" in asked:
            target.CharWeight = 150.0 if asked["bold"] else 100.0
        if "italic" in asked:
            target.CharPosture = uno.Enum(
                "com.sun.star.awt.FontSlant",
                "ITALIC" if asked["italic"] else "NONE")
        if "underline" in asked:
            target.CharUnderline = 1 if asked["underline"] else 0
        if "font_size" in asked:
            target.CharHeight = asked["font_size"]
        if "font_name" in asked:
            target.CharFontName = asked["font_name"]
        if "color" in asked:
            target.CharColor = _colour(asked["color"])
        if "background_color" in asked:
            target.CharBackColor = _colour(asked["background_color"])
        if "character_style" in asked:
            target.CharStyleName = asked["character_style"]
        if "link" in asked:
            target.HyperLinkURL = asked["link"]
            target.HyperLinkTarget = asked.get("link_target", "")
            # Without these the link works but does not look like one.
            target.UnvisitedCharStyleName = UNVISITED_LINK_STYLE
            target.VisitedCharStyleName = VISITED_LINK_STYLE

    def _formatting_of(self, run: Dict[str, Any]) -> Dict[str, Any]:
        """The character properties a run asked for, validated"""
        asked = {}
        for key in ("bold", "italic", "underline"):
            if run.get(key) is not None:
                asked[key] = bool(run[key])
        if run.get("font_size") is not None:
            asked["font_size"] = float(run["font_size"])
        if run.get("font_name") is not None:
            asked["font_name"] = str(run["font_name"])
        if run.get("color") is not None:
            asked["color"] = _colour_name(_colour(run["color"]))
        if run.get("background_color") is not None:
            asked["background_color"] = _colour_name(_colour(run["background_color"]))
        for key in ("link", "link_target", "character_style"):
            if run.get(key) is not None:
                if not isinstance(run[key], str):
                    raise AddressError(
                        f"{key} must be a string, got {run[key]!r}")
                asked[key] = run[key]
        return asked

    def format_paragraph(self, address: Any, background_color: Any = None,
                         border: Optional[bool] = None,
                         border_color: Any = "#808080",
                         border_width: float = 0.5,
                         padding: Optional[float] = None,
                         track_changes: Optional[bool] = None,
                         doc: Any = None) -> Dict[str, Any]:
        """
        Put a frame and a background behind a paragraph

        Borders live on the range as TopBorder/BottomBorder/LeftBorder/
        RightBorder — *not* ParaTopBorder, which does not exist and is the
        wrong guess to make. Consecutive paragraphs given the same border are
        drawn as one box, because ParaIsConnectBorder defaults to true, so a
        code block is framed by formatting each of its paragraphs.

        The background is the other trap: ParaBackColor cannot be written,
        neither on a cursor nor on the paragraph. FillStyle plus FillColor on
        the paragraph object is what works, and what survives saving.
        """
        doc, error = self._writer_document(doc, "Formatting a paragraph")
        if error:
            return error

        asked = {}
        try:
            if background_color is not None:
                asked["background_color"] = _colour_name(_colour(background_color))
            if border is not None:
                asked["border"] = bool(border)
                asked["border_color"] = _colour_name(_colour(border_color))
                asked["border_width"] = float(border_width)
            if padding is not None:
                asked["padding"] = float(padding)
        except AddressError as e:
            return refusal("INVALID_ADDRESS", e)

        if not asked:
            return {"success": False, "code": "INVALID_PARAMETER",
                    "error": "Nothing to apply: pass background_color, border "
                             "or padding"}

        def edit():
            target = self._resolve_address(doc, address)

            if "border" in asked:
                line = _border_line(
                    _colour(asked["border_color"]),
                    asked["border_width"] if asked["border"] else 0.0)
                for side in ("TopBorder", "BottomBorder",
                             "LeftBorder", "RightBorder"):
                    setattr(target, side, line)

            if "padding" in asked:
                distance = _points_to_uno(asked["padding"])
                for side in ("TopBorderDistance", "BottomBorderDistance",
                             "LeftBorderDistance", "RightBorderDistance"):
                    setattr(target, side, distance)

            if "background_color" in asked:
                self._fill_paragraphs(doc, target,
                                      _colour(asked["background_color"]))

            return {"applied": asked}

        return self._guarded_edit(doc, "MCP: format paragraph",
                                  track_changes, edit)

    def _fill_paragraphs(self, doc: Any, target: Any, colour: int):
        """
        Give every paragraph the range touches a background

        The fill has to be set on the paragraph objects themselves: writing
        ParaBackColor on a text range is silently ignored.
        """
        start = self._locate_range(doc, target.getStart())[0]["paragraph"]
        end = self._locate_range(doc, target.getEnd())[0]["paragraph"]
        if start is None:
            raise AddressError("that address is outside the body text, so its "
                               "paragraphs cannot be filled")
        if end is None:
            end = start

        for paragraph, index in self._body_paragraphs(doc):
            if start <= index <= end:
                paragraph.FillStyle = uno.Enum("com.sun.star.drawing.FillStyle",
                                               "SOLID")
                paragraph.FillColor = colour

    def apply_paragraph_style(self, address: Any, style: str,
                              track_changes: Optional[bool] = None,
                              doc: Any = None) -> Dict[str, Any]:
        """
        Give the paragraphs at an address a paragraph style

        This is the reliable way to make a block monospace: "Preformatted Text"
        carries the font and the spacing together, where setting a font by hand
        leaves the paragraph spacing of body text.

        A style the document does not have throws deep inside UNO, so the name
        is checked first and the caller is pointed at list_styles.
        """
        doc, error = self._writer_document(doc, "Applying a paragraph style")
        if error:
            return error

        if not isinstance(style, str) or not style:
            return {"success": False, "code": "INVALID_PARAMETER", "error": "style must be a non-empty string"}

        if not self._has_style(doc, "ParagraphStyles", style):
            return {
                "success": False,
                "code": "NOT_FOUND",
                "error": f"This document has no paragraph style {style!r}. "
                         f"Use list_styles to see the names it does have."
            }

        def edit():
            target = self._resolve_address(doc, address)
            target.ParaStyleName = style
            return {"style": style}

        return self._guarded_edit(doc, "MCP: apply paragraph style",
                                  track_changes, edit)

    def list_styles(self, family: str = "ParagraphStyles",
                    doc: Any = None) -> Dict[str, Any]:
        """The style names a document actually has, so callers stop guessing"""
        doc, error = self._writer_document(doc, "Listing styles")
        if error:
            return error

        try:
            families = doc.StyleFamilies
            if not families.hasByName(family):
                return {
                    "success": False,
                    "code": "NOT_FOUND",
                    "error": f"No style family {family!r}. This document has: "
                             f"{', '.join(families.getElementNames())}"
                }
            names = list(families.getByName(family).getElementNames())
        except Exception as e:
            logger.error(f"Could not list styles: {e}")
            return refusal("FAILED", e)

        return {"success": True, "family": family, "styles": names,
                "count": len(names)}

    def _has_style(self, doc: Any, family: str, style: str) -> bool:
        """Whether a document carries a style, False if it cannot be asked"""
        try:
            return bool(doc.StyleFamilies.getByName(family).hasByName(style))
        except Exception as e:
            logger.info(f"Could not check style {style!r}: {e}")
            return False

    def _style_family(self, doc: Any, family: str) -> tuple:
        """(the UNO family, its name) for "paragraph", "character", …"""
        if not isinstance(family, str) or family.lower() not in STYLE_FAMILIES:
            raise AddressError(
                f"family must be one of {', '.join(sorted(STYLE_FAMILIES))}, "
                f"got {family!r}")
        uno_name = STYLE_FAMILIES[family.lower()]
        try:
            return doc.StyleFamilies.getByName(uno_name), uno_name
        except Exception as e:
            raise AddressError(f"this document has no {uno_name}: {e}")

    def _style_at(self, doc: Any, address: Any, family: str) -> str:
        """The name of the style the text at an address uses"""
        wanted = family.lower()
        if wanted == "paragraph":
            located, _, _ = self._locate_range(
                doc, self._resolve_address(doc, address))
            paragraph = self._paragraph_of(doc, located)
            if paragraph is None:
                raise AddressError("that address is in no paragraph whose "
                                   "style could be read")
            return _get_property(paragraph, "ParaStyleName", "") or ""
        if wanted == "character":
            target = self._resolve_address(doc, address)
            name = _get_property(target, "CharStyleName", "") or ""
            if not name:
                raise AddressError(
                    "that text carries no character style of its own, so its "
                    "look comes from its paragraph style — ask for family "
                    "\"paragraph\"")
            return name
        if wanted == "page":
            controller = doc.getCurrentController()
            view = controller.getViewCursor() if controller else None
            name = _get_property(view, "PageStyleName", "") or ""
            if not name:
                raise AddressError("the view does not say which page style is "
                                   "in use")
            return name
        raise AddressError(f"a {family} style cannot be found from an address; "
                           f"name it instead")

    def describe_style(self, name: Optional[str] = None,
                       family: str = "paragraph", address: Any = None,
                       all_properties: bool = False,
                       doc: Any = None) -> Dict[str, Any]:
        """
        Everything about one style: what it sets itself, and what is in force

        `set_here` is the style's own definition — the properties UNO reports
        as DIRECT_VALUE — which is the same handful that appears in the
        document's styles.xml, without reading the file. `effective` is what
        the text actually gets, each value saying whether it comes from this
        style or is inherited, so the two questions a reader has are
        answered separately.

        With no `name`, the style used at `address` is described, or the one
        at the caret — which is what "this style" means.
        """
        doc, error = self._writer_document(doc, "Describing a style")
        if error:
            return error

        try:
            styles, family_name = self._style_family(doc, family)
            if not name:
                name = self._style_at(doc, address
                                      if address is not None
                                      else {"selection": True}, family)
        except AddressError as e:
            return refusal("INVALID_ADDRESS", e)

        if not styles.hasByName(name):
            return {"success": False, "code": "NOT_FOUND",
                    "error": f"there is no {family} style called {name!r} in "
                             f"this document; list_styles reports what there "
                             f"is"}
        style = styles.getByName(name)

        described = {"name": name, "family": family.lower(),
                     "uno_family": family_name}
        for key, prop in (("display_name", "DisplayName"),
                          ("parent", "ParentStyle"),
                          ("next_style", "FollowStyle"),
                          ("linked_style", "LinkStyle"),
                          ("category", "Category"),
                          ("auto_update", "IsAutoUpdate"),
                          ("hidden", "Hidden")):
            value = _get_property(style, prop, None)
            described[key] = _style_value(prop, value) if value is not None \
                else None
        for key, method in (("user_defined", "isUserDefined"),
                            ("in_use", "isInUse")):
            try:
                described[key] = bool(getattr(style, method)())
            except Exception as e:
                logger.info(f"Could not ask a style {method}: {e}")
                described[key] = None

        # The chain of parents, so "where does this come from" has an answer.
        chain = []
        current = style
        while len(chain) < 20:
            parent = _get_property(current, "ParentStyle", "") or ""
            if not parent or not styles.hasByName(parent):
                break
            chain.append(parent)
            current = styles.getByName(parent)
        described["inherits_from"] = chain

        try:
            properties = [entry.Name for entry
                          in style.getPropertySetInfo().getProperties()]
        except Exception as e:
            logger.error(f"Could not list the properties of {name}: {e}")
            return refusal("FAILED", e)

        set_here, effective, everything = {}, {}, {}
        for prop in sorted(properties):
            state = None
            try:
                state = _get_property_state(style, prop)
                value = getattr(style, prop)
            except Exception:
                continue                    # a property this style will not show
            readable = _style_value(prop, value)
            if state == "DIRECT_VALUE":
                set_here[prop] = {"value": readable, "raw": _raw(value)}
            if prop in STYLE_EFFECTIVE:
                effective[prop] = {"value": readable,
                                   "from": "this style"
                                   if state == "DIRECT_VALUE" else "inherited"}
            if all_properties:
                everything[prop] = {"value": readable, "state": state}

        result = {"success": True, "style": described,
                  "set_here": set_here, "set_here_count": len(set_here),
                  "effective": effective,
                  "properties_in_all": len(properties)}
        if all_properties:
            result["all_properties"] = everything
        return result
