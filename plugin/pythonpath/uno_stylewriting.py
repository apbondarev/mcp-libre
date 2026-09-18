"""Writing styles: making one, changing it, renaming it, replacing it.

`describe_style` reads a style's own definition; this is the other half, and
it is what "make this document use our house styles" needs. Measured on a
live Writer:

  * a style is created as `com.sun.star.style.ParagraphStyle` (or the
    character one) and put in its family with `insertByName`; `ParentStyle`
    is what it inherits from, and `isUserDefined()` is then true;
  * `setName` **renames it in place and the text follows** — a paragraph
    wearing it reported the new name straight afterwards;
  * `removeByName` takes a style away and **the text falls back to its
    parent** — a paragraph in a removed style read "Preformatted Text", the
    style it was based on;
  * removing a **built-in** style is accepted and does nothing: no
    exception, no removal. So a style that is not `isUserDefined()` is
    refused here rather than letting a caller believe it went;
  * replacing one style with another throughout is Writer's own search for a
    paragraph style plus a write per hit, since `ReplaceStyles` is not a
    thing the descriptor offers.
"""

from typing import Any, Dict, List, Optional
import logging

from uno_values import (AddressError, STYLE_FAMILIES, _colour, _locale,
                        _points_to_uno, _get_property, refusal)

logger = logging.getLogger(__name__)

# The properties a style can be given, in the words the formatting tools use.
CHARACTER_PROPERTIES = {
    "bold": ("CharWeight", lambda v: 150.0 if v else 100.0),
    "italic": ("CharPosture", None),          # handled with an enum below
    "underline": ("CharUnderline", lambda v: 1 if v else 0),
    "font_size": ("CharHeight", lambda v: float(v)),
    "font_name": ("CharFontName", lambda v: str(v)),
    "color": ("CharColor", _colour),
    "background_color": ("CharBackColor", _colour),
    "language": ("CharLocale", None),         # a locale struct
}
PARAGRAPH_PROPERTIES = {
    "alignment": ("ParaAdjust", None),        # a word, mapped below
    "space_above_pt": ("ParaTopMargin", _points_to_uno),
    "space_below_pt": ("ParaBottomMargin", _points_to_uno),
    "indent_left_mm": ("ParaLeftMargin", lambda v: int(round(float(v) * 100))),
    "indent_right_mm": ("ParaRightMargin",
                        lambda v: int(round(float(v) * 100))),
    "first_line_indent_mm": ("ParaFirstLineIndent",
                             lambda v: int(round(float(v) * 100))),
    "keep_with_next": ("ParaKeepTogether", bool),
    "background_color": ("FillColor", _colour),
}
ALIGNMENTS = {"left": 0, "right": 1, "center": 3, "centre": 3, "justify": 2}


class StyleWritingMixin:
    """Part of UNOBridge — see uno_bridge.py for how the parts meet."""

    def _style_family_of(self, doc: Any, family: str) -> tuple:
        wanted = (family or "paragraph").lower()
        if wanted not in ("paragraph", "character"):
            raise AddressError(
                f"family is \"paragraph\" or \"character\" — the others are "
                f"read-only here; got {family!r}")
        return doc.StyleFamilies.getByName(STYLE_FAMILIES[wanted]), wanted

    def _style_writes(self, properties: Any, family: str) -> List[tuple]:
        """[(UNO name, value)] for what was asked, or AddressError.

        Worked out **before** anything is written: a bad property name was
        discovered half way through, after the style had already been put in
        its family — the call refused and the style stayed behind.
        """
        import uno as _uno

        known = dict(CHARACTER_PROPERTIES)
        if family == "paragraph":
            known.update(PARAGRAPH_PROPERTIES)
        if properties and not isinstance(properties, dict):
            raise AddressError("properties is an object of names and values")
        writes = []
        for name, value in (properties or {}).items():
            if name not in known:
                raise AddressError(
                    f"a style has nothing called {name!r} here; the names are "
                    f"{', '.join(sorted(known))}")
            uno_name, convert = known[name]
            if name == "italic":
                writes.append((uno_name,
                               _uno.Enum("com.sun.star.awt.FontSlant",
                                         "ITALIC" if value else "NONE")))
            elif name == "alignment":
                if value not in ALIGNMENTS:
                    raise AddressError(
                        f"alignment is one of "
                        f"{', '.join(sorted(ALIGNMENTS))}, got {value!r}")
                writes.append(("ParaAdjust", ALIGNMENTS[value]))
            elif name == "language":
                writes.append(("CharLocale", _locale(value)))
            elif name == "background_color" and family == "paragraph":
                # A paragraph style takes a fill the way a paragraph does.
                writes.append(("FillStyle",
                               _uno.Enum("com.sun.star.drawing.FillStyle",
                                         "SOLID")))
                writes.append(("FillColor", _colour(value)))
            else:
                try:
                    writes.append((uno_name,
                                   convert(value) if convert else value))
                except AddressError:
                    raise
                except Exception as e:
                    raise AddressError(f"{name}: {e}")
        return writes

    def _apply_style_properties(self, style: Any,
                                writes: List[tuple]) -> None:
        """Write what _style_writes worked out"""
        for uno_name, value in writes:
            setattr(style, uno_name, value)

    def create_style(self, name: str, family: str = "paragraph",
                     based_on: Optional[str] = None,
                     from_style: Optional[str] = None,
                     next_style: Optional[str] = None,
                     properties: Optional[Dict[str, Any]] = None,
                     doc: Any = None) -> Dict[str, Any]:
        """
        Make a style, on its own or from one this document already has

        `from_style` copies what that style sets itself — its definition, the
        properties describe_style reports under `set_here` — so a house style
        can be cloned and then changed, rather than described by hand.
        """
        doc, error = self._writer_document(doc, "Creating a style")
        if error:
            return error
        if not isinstance(name, str) or not name.strip():
            return refusal("INVALID_PARAMETER", "a style needs a name")
        name = name.strip()
        try:
            styles, wanted = self._style_family_of(doc, family)
        except AddressError as e:
            return refusal("INVALID_PARAMETER", e)
        if styles.hasByName(name):
            return refusal("INVALID_PARAMETER",
                           f"this document already has a {wanted} style "
                           f"called {name!r}; update_style changes one")
        for other in (based_on, from_style, next_style):
            if other is not None and not styles.hasByName(other):
                return refusal("NOT_FOUND",
                               f"no {wanted} style called {other!r}; "
                               f"list_styles says which there are")

        service = ("com.sun.star.style.ParagraphStyle"
                   if wanted == "paragraph"
                   else "com.sun.star.style.CharacterStyle")
        try:
            writes = self._style_writes(properties, wanted)
        except AddressError as e:
            return refusal("INVALID_PARAMETER", e)

        def edit():
            style = doc.createInstance(service)
            styles.insertByName(name, style)
            copied = []
            if from_style is not None:
                copied = self._copy_definition(styles.getByName(from_style),
                                               style)
            if based_on is not None:
                style.ParentStyle = based_on
            if next_style is not None and wanted == "paragraph":
                style.FollowStyle = next_style
            self._apply_style_properties(style, writes)
            return {"name": name, "family": wanted,
                    "based_on": _get_property(style, "ParentStyle", None),
                    "next_style": _get_property(style, "FollowStyle", None),
                    "copied_from": from_style,
                    "copied_properties": len(copied),
                    "set": sorted(properties or {})}

        return self._guarded_edit(doc, f"MCP: create the style {name}", None,
                                  edit)

    def _copy_definition(self, source: Any, target: Any) -> List[str]:
        """Copy what a style sets itself — its DIRECT_VALUE properties"""
        copied = []
        try:
            names = [entry.Name for entry
                     in source.getPropertySetInfo().getProperties()]
        except Exception as e:
            logger.info(f"Could not list the properties of a style: {e}")
            return copied
        for name in names:
            if name in ("Name", "DisplayName", "ParentStyle", "FollowStyle",
                        "IsPhysical", "UserDefinedAttributes", "Category"):
                continue
            try:
                if source.getPropertyState(name).value != "DIRECT_VALUE":
                    continue
                setattr(target, name, getattr(source, name))
                copied.append(name)
            except Exception as e:
                logger.info(f"Could not copy {name}: {e}")
        return copied

    def update_style(self, name: str, family: str = "paragraph",
                     properties: Optional[Dict[str, Any]] = None,
                     based_on: Optional[str] = None,
                     next_style: Optional[str] = None,
                     doc: Any = None) -> Dict[str, Any]:
        """
        Change what a style says — every paragraph wearing it follows

        A built-in style can be changed like any other; only removing one is
        refused.
        """
        doc, error = self._writer_document(doc, "Updating a style")
        if error:
            return error
        try:
            styles, wanted = self._style_family_of(doc, family)
        except AddressError as e:
            return refusal("INVALID_PARAMETER", e)
        if not styles.hasByName(name):
            return refusal("NOT_FOUND",
                           f"no {wanted} style called {name!r}; list_styles "
                           f"says which there are")
        if not properties and based_on is None and next_style is None:
            return refusal("INVALID_PARAMETER",
                           "say what to change: properties, based_on or "
                           "next_style")
        for other in (based_on, next_style):
            if other is not None and not styles.hasByName(other):
                return refusal("NOT_FOUND",
                               f"no {wanted} style called {other!r}")
        style = styles.getByName(name)
        try:
            writes = self._style_writes(properties, wanted)
        except AddressError as e:
            return refusal("INVALID_PARAMETER", e)

        def edit():
            if based_on is not None:
                style.ParentStyle = based_on
            if next_style is not None and wanted == "paragraph":
                style.FollowStyle = next_style
            self._apply_style_properties(style, writes)
            return {"name": name, "family": wanted,
                    "set": sorted(properties or {}),
                    "based_on": _get_property(style, "ParentStyle", None),
                    "next_style": _get_property(style, "FollowStyle", None),
                    "built_in": not self._user_defined(style)}

        return self._guarded_edit(doc, f"MCP: update the style {name}", None,
                                  edit)

    def _user_defined(self, style: Any) -> bool:
        try:
            return bool(style.isUserDefined())
        except Exception as e:
            logger.info(f"A style would not say whether it is its own: {e}")
            return False

    def rename_style(self, name: str, new_name: str,
                     family: str = "paragraph",
                     doc: Any = None) -> Dict[str, Any]:
        """
        Give a style another name; the text wearing it follows by itself

        Only a style this document defines can be renamed — a built-in one
        keeps the name the office knows it by.
        """
        doc, error = self._writer_document(doc, "Renaming a style")
        if error:
            return error
        try:
            styles, wanted = self._style_family_of(doc, family)
        except AddressError as e:
            return refusal("INVALID_PARAMETER", e)
        if not styles.hasByName(name):
            return refusal("NOT_FOUND",
                           f"no {wanted} style called {name!r}")
        if not isinstance(new_name, str) or not new_name.strip():
            return refusal("INVALID_PARAMETER", "a style needs a name")
        new_name = new_name.strip()
        if styles.hasByName(new_name):
            return refusal("INVALID_PARAMETER",
                           f"this document already has a {wanted} style "
                           f"called {new_name!r}")
        style = styles.getByName(name)
        if not self._user_defined(style):
            return refusal("INVALID_PARAMETER",
                           f"{name!r} is one of the office's own styles and "
                           f"cannot be renamed; create_style with "
                           f"from_style={name!r} makes a copy you own")

        def edit():
            style.setName(new_name)
            return {"renamed": name, "to": new_name, "family": wanted}

        return self._guarded_edit(doc, "MCP: rename a style", None, edit)

    def delete_style(self, name: str, family: str = "paragraph",
                     replace_with: Optional[str] = None,
                     doc: Any = None) -> Dict[str, Any]:
        """
        Remove a style this document defines

        The text wearing it falls back to the style it was based on —
        measured — unless `replace_with` names what it should wear instead,
        which is done first. A built-in style is refused: UNO accepts the
        removal, does nothing, and says nothing.
        """
        doc, error = self._writer_document(doc, "Deleting a style")
        if error:
            return error
        try:
            styles, wanted = self._style_family_of(doc, family)
        except AddressError as e:
            return refusal("INVALID_PARAMETER", e)
        if not styles.hasByName(name):
            return refusal("NOT_FOUND",
                           f"no {wanted} style called {name!r}")
        style = styles.getByName(name)
        if not self._user_defined(style):
            return refusal("INVALID_PARAMETER",
                           f"{name!r} is one of the office's own styles, and "
                           f"UNO accepts removing it while leaving it exactly "
                           f"where it was — so this refuses rather than "
                           f"pretending")
        if replace_with is not None and not styles.hasByName(replace_with):
            return refusal("NOT_FOUND",
                           f"no {wanted} style called {replace_with!r} to put "
                           f"in its place")
        parent = _get_property(style, "ParentStyle", None)

        def edit():
            swapped = 0
            if replace_with is not None:
                swapped = self._swap_style(doc, name, replace_with, wanted,
                                           None)
            styles.removeByName(name)
            return {"deleted": name, "family": wanted,
                    "text_moved_to": replace_with or parent,
                    "places_changed": swapped}

        return self._guarded_edit(doc, f"MCP: delete the style {name}", None,
                                  edit)

    def replace_style(self, name: str, with_style: str,
                      family: str = "paragraph", address: Any = None,
                      track_changes: Optional[bool] = None,
                      doc: Any = None) -> Dict[str, Any]:
        """
        Put one style in the place of another, throughout or in a part

        This is what "make this document use our house styles" is made of:
        every paragraph in the old style comes out in the new one, and
        nothing else about the text changes.
        """
        doc, error = self._writer_document(doc, "Replacing a style")
        if error:
            return error
        try:
            styles, wanted = self._style_family_of(doc, family)
        except AddressError as e:
            return refusal("INVALID_PARAMETER", e)
        for one in (name, with_style):
            if not styles.hasByName(one):
                return refusal("NOT_FOUND",
                               f"no {wanted} style called {one!r}; "
                               f"list_styles says which there are")
        if name == with_style:
            return refusal("INVALID_PARAMETER",
                           "that would put a style in its own place")

        def edit():
            changed = self._swap_style(doc, name, with_style, wanted, address)
            return {"replaced": name, "with": with_style, "family": wanted,
                    "places_changed": changed}

        return self._guarded_edit(doc, f"MCP: {name} → {with_style}",
                                  track_changes, edit)

    def _swap_style(self, doc: Any, name: str, with_style: str, family: str,
                    address: Any) -> int:
        """Write the new style wherever the old one is worn, in one walk.

        Finding the places and then resolving each address cost a walk of the
        body per place: 184 code blocks in a real document took 20.5s that
        way. One walk sets them all — a paragraph carries `ParaStyleName`
        itself, and a portion is a range that carries `CharStyleName`.
        """
        try:
            covers, scope = self._comment_scope(doc, address)
        except Exception as e:
            logger.info(f"Could not read the scope: {e}")
            return 0
        window = self._window_of(scope) if address is not None else None
        first, last = window if window else (0, None)

        changed = 0
        try:
            paragraphs = doc.getText().createEnumeration()
        except Exception as e:
            logger.error(f"Could not walk the document: {e}")
            return 0
        index = 0
        while paragraphs.hasMoreElements():
            paragraph = paragraphs.nextElement()
            if not hasattr(paragraph, "createEnumeration"):
                continue
            if last is not None and index > last:
                break
            if index < first or not covers({"paragraph": index, "offset": 0,
                                            "length": 0}):
                index += 1
                continue
            if family == "paragraph":
                if (_get_property(paragraph, "ParaStyleName", "") or "") == name:
                    try:
                        paragraph.ParaStyleName = with_style
                        changed += 1
                    except Exception as e:
                        logger.info(f"Could not restyle paragraph {index}: {e}")
            else:
                portions = paragraph.createEnumeration()
                while portions.hasMoreElements():
                    portion = portions.nextElement()
                    if (_get_property(portion, "CharStyleName", "")
                            or "") != name:
                        continue
                    try:
                        portion.CharStyleName = with_style
                        changed += 1
                    except Exception as e:
                        logger.info(f"Could not restyle a run: {e}")
            index += 1

        # A paragraph inside a table cell is not a body paragraph, and
        # Writer's own search does find those — 184 code blocks against the
        # body's 162 on a real document. "Throughout" has to mean it.
        if address is None:
            changed += self._swap_in_tables(doc, name, with_style, family)
        return changed

    def _swap_in_tables(self, doc: Any, name: str, with_style: str,
                        family: str) -> int:
        """The same swap inside every table cell"""
        changed = 0
        try:
            tables = doc.getTextTables()
        except Exception as e:
            logger.info(f"This document keeps no tables: {e}")
            return 0
        for table_name in tables.getElementNames():
            try:
                table = tables.getByName(table_name)
                cells = table.getCellNames()
            except Exception as e:
                logger.info(f"Could not read the table {table_name}: {e}")
                continue
            for cell_name in cells:
                try:
                    cell = table.getCellByName(cell_name)
                    paragraphs = cell.createEnumeration()
                except Exception as e:
                    logger.info(f"Could not read the cell {cell_name}: {e}")
                    continue
                while paragraphs.hasMoreElements():
                    paragraph = paragraphs.nextElement()
                    if not hasattr(paragraph, "createEnumeration"):
                        continue
                    if family == "paragraph":
                        if (_get_property(paragraph, "ParaStyleName", "")
                                or "") == name:
                            try:
                                paragraph.ParaStyleName = with_style
                                changed += 1
                            except Exception as e:
                                logger.info(f"Could not restyle a cell's "
                                            f"paragraph: {e}")
                        continue
                    portions = paragraph.createEnumeration()
                    while portions.hasMoreElements():
                        portion = portions.nextElement()
                        if (_get_property(portion, "CharStyleName", "")
                                or "") != name:
                            continue
                        try:
                            portion.CharStyleName = with_style
                            changed += 1
                        except Exception as e:
                            logger.info(f"Could not restyle a run in a "
                                        f"cell: {e}")
        return changed
