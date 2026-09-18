"""Page layout: the size of the page, its margins, its columns and its breaks.

All of it lives in a **page style**, except the break, which is a property of
the paragraph that starts the new page. Measured on a live Writer:

  * **`IsLandscape` turns nothing.** Setting it true left Width 21001 and
    Height 29700 — the page stayed upright and only the flag moved. The size
    has to be swapped as well, which is what `set_page_layout` does;
  * page measurements **round**: A4 reports a width of 21001 rather than
    21000, and a right margin written as 1500 reads back as 1499. Nothing
    here compares them for equality, and millimetres are reported to two
    decimals rather than pretended exact;
  * `TextColumns.getColumnCount()` on a page style is **0** both before any
    columns are set and after setting it to one, so one column is one;
  * a page break is `BreakType` on the paragraph — an enum whose `.value`
    reads "PAGE_BEFORE" — and the page style a break switches to is
    `PageDescName` beside it. **Clearing that with None throws**
    (`CannotConvertException: Type 0 is not supported`); an empty string
    clears it, and reads back as None. `PageNumberOffset` restarts the
    numbering, and it too refuses None;
  * line numbering is a document-level `LineNumberingProperties`, and its
    `Separator` **throws on read** while every other property answers.
"""

from typing import Any, Dict, Optional
import logging

from uno_values import AddressError, _get_property, refusal

logger = logging.getLogger(__name__)

# The papers a caller can name, in 1/100 mm, upright.
PAPERS = {
    "a3": (29700, 42000), "a4": (21000, 29700), "a5": (14800, 21000),
    "letter": (21590, 27940), "legal": (21590, 35560),
}

BREAKS = {"none": "NONE", "page_before": "PAGE_BEFORE",
          "page_after": "PAGE_AFTER", "column_before": "COLUMN_BEFORE",
          "column_after": "COLUMN_AFTER"}

MARGINS = {"top": "TopMargin", "bottom": "BottomMargin",
           "left": "LeftMargin", "right": "RightMargin"}


def _mm(hundredths: Any) -> Optional[float]:
    """1/100 mm as millimetres, rounded the way a person would read it"""
    if not isinstance(hundredths, (int, float)) or isinstance(hundredths, bool):
        return None
    return round(hundredths / 100.0, 2)


class LayoutMixin:
    """Part of UNOBridge — see uno_bridge.py for how the parts meet."""

    def _page_layout_of(self, style: Any, name: str) -> Dict[str, Any]:
        width = _get_property(style, "Width", None)
        height = _get_property(style, "Height", None)
        columns = 1
        try:
            columns = max(1, style.TextColumns.getColumnCount())
        except Exception as e:
            logger.info(f"A page style would not say its columns: {e}")
        return {
            "page_style": name,
            "width_mm": _mm(width), "height_mm": _mm(height),
            "orientation": "landscape" if _get_property(style, "IsLandscape",
                                                        False) else "portrait",
            "paper": self._paper_named(width, height),
            "margins_mm": {side: _mm(_get_property(style, prop, None))
                           for side, prop in MARGINS.items()},
            "columns": columns,
            "gutter_mm": _mm(_get_property(style, "GutterMargin", None)),
            "follow_style": _get_property(style, "FollowStyle", None),
        }

    def _paper_named(self, width: Any, height: Any) -> Optional[str]:
        """Which paper this is, allowing for the rounding UNO does"""
        if not isinstance(width, int) or not isinstance(height, int):
            return None
        for paper, (paper_width, paper_height) in PAPERS.items():
            for one, other in ((paper_width, paper_height),
                               (paper_height, paper_width)):
                if abs(width - one) <= 5 and abs(height - other) <= 5:
                    return paper
        return None

    def get_page_layout(self, page_style: Optional[str] = None,
                        doc: Any = None) -> Dict[str, Any]:
        """
        The size, orientation, margins and columns of a page style

        Sizes come back in millimetres, which is what a caller means; the
        raw 1/100 mm underneath rounds, so they are never exact.
        """
        doc, error = self._writer_document(doc, "Reading the page layout")
        if error:
            return error
        style, name, refused = self._page_style(doc, page_style)
        if refused:
            return refused

        layout = self._page_layout_of(style, name)
        layout["success"] = True
        layout["line_numbering"] = self._line_numbering_of(doc)
        return layout

    def _line_numbering_of(self, doc: Any) -> Dict[str, Any]:
        settings = _get_property(doc, "LineNumberingProperties", None)
        if settings is None:
            return {"on": False}
        # Separator throws on read — measured — so it is not asked for.
        return {
            "on": bool(_get_property(settings, "IsOn", False)),
            "interval": _get_property(settings, "Interval", None),
            "distance_mm": _mm(_get_property(settings, "Distance", None)),
            "restart_each_page": bool(_get_property(settings,
                                                    "RestartAtEachPage",
                                                    False)),
            "count_empty_lines": bool(_get_property(settings,
                                                    "CountEmptyLines", True)),
        }

    def set_page_layout(self, page_style: Optional[str] = None,
                        paper: Optional[str] = None,
                        width_mm: Optional[float] = None,
                        height_mm: Optional[float] = None,
                        orientation: Optional[str] = None,
                        margins_mm: Optional[Dict[str, Any]] = None,
                        columns: Optional[int] = None,
                        gutter_mm: Optional[float] = None,
                        track_changes: Optional[bool] = None,
                        doc: Any = None) -> Dict[str, Any]:
        """
        Set the size, orientation, margins or columns of a page style

        Orientation is done properly: `IsLandscape` on its own turns nothing
        — measured — so the width and the height are swapped to match it.
        """
        doc, error = self._writer_document(doc, "Setting the page layout")
        if error:
            return error
        if paper is not None and paper.lower() not in PAPERS:
            return refusal("INVALID_PARAMETER",
                           f"paper is one of {', '.join(sorted(PAPERS))}, got "
                           f"{paper!r}")
        if orientation is not None and orientation not in ("portrait",
                                                           "landscape"):
            return refusal("INVALID_PARAMETER",
                           f"orientation is \"portrait\" or \"landscape\", got "
                           f"{orientation!r}")
        if columns is not None and (isinstance(columns, bool)
                                    or not isinstance(columns, int)
                                    or not 1 <= columns <= 99):
            return refusal("INVALID_PARAMETER",
                           f"columns is a number from 1 to 99, got {columns!r}")
        if margins_mm is not None:
            if not isinstance(margins_mm, dict):
                return refusal("INVALID_PARAMETER",
                               "margins_mm is an object of top, bottom, left "
                               "and right, in millimetres")
            unknown = [side for side in margins_mm if side not in MARGINS]
            if unknown:
                return refusal("INVALID_PARAMETER",
                               f"a margin is top, bottom, left or right; got "
                               f"{unknown[0]!r}")
        if all(one is None for one in (paper, width_mm, height_mm, orientation,
                                       margins_mm, columns, gutter_mm)):
            return refusal("INVALID_PARAMETER",
                           "say what to change: paper, width_mm, height_mm, "
                           "orientation, margins_mm, columns or gutter_mm")

        style, name, refused = self._page_style(doc, page_style)
        if refused:
            return refused
        was = self._page_layout_of(style, name)

        def edit():
            width = _get_property(style, "Width", 0)
            height = _get_property(style, "Height", 0)
            if paper is not None:
                width, height = PAPERS[paper.lower()]
            if width_mm is not None:
                width = int(round(float(width_mm) * 100))
            if height_mm is not None:
                height = int(round(float(height_mm) * 100))
            if orientation is not None:
                # Measured: the flag alone leaves the page upright, so the
                # size is swapped to match what was asked for.
                wants_wide = orientation == "landscape"
                if wants_wide != (width > height):
                    width, height = height, width
                style.IsLandscape = wants_wide
            if (width, height) != (_get_property(style, "Width", 0),
                                   _get_property(style, "Height", 0)):
                import uno as _uno
                size = _uno.createUnoStruct("com.sun.star.awt.Size")
                size.Width, size.Height = width, height
                style.Size = size
            for side, value in (margins_mm or {}).items():
                setattr(style, MARGINS[side], int(round(float(value) * 100)))
            if gutter_mm is not None:
                style.GutterMargin = int(round(float(gutter_mm) * 100))
            if columns is not None:
                settings = doc.createInstance("com.sun.star.text.TextColumns")
                settings.setColumnCount(columns)
                style.TextColumns = settings
            now = self._page_layout_of(style, name)
            now["was"] = was
            return now

        return self._guarded_edit(doc, "MCP: set the page layout",
                                  track_changes, edit)

    def set_page_break(self, address: Any, kind: str = "page_before",
                       page_style: Optional[str] = None,
                       page_number: Optional[int] = None,
                       track_changes: Optional[bool] = None,
                       doc: Any = None) -> Dict[str, Any]:
        """
        Start a new page at a paragraph, or take that break away

        A break belongs to the paragraph after it, and it can switch the page
        style at the same time — which is how a document changes from
        portrait to landscape half way through.
        """
        doc, error = self._writer_document(doc, "Setting a page break")
        if error:
            return error
        if kind not in BREAKS:
            return refusal("INVALID_PARAMETER",
                           f"kind is one of {', '.join(sorted(BREAKS))}, got "
                           f"{kind!r}")
        if page_style is not None:
            styles = self._page_styles(doc)
            if styles is None or not styles.hasByName(page_style):
                return refusal("NOT_FOUND",
                               f"no page style called {page_style!r}")
            if kind == "none":
                return refusal("INVALID_PARAMETER",
                               "a page style can only be switched at a break; "
                               "kind cannot be \"none\" then")
        if page_number is not None and (isinstance(page_number, bool)
                                        or not isinstance(page_number, int)
                                        or page_number < 1):
            return refusal("INVALID_PARAMETER",
                           f"page_number is the number the new page takes, 1 "
                           f"or more, got {page_number!r}")

        try:
            index = self._paragraph_index_of(doc, address)
        except AddressError as e:
            return refusal("INVALID_ADDRESS", e)
        paragraph = self._paragraph_at(doc.getText(), index)
        if paragraph is None:
            return refusal("INVALID_ADDRESS", f"no body paragraph {index}")

        def edit():
            # pyuno hands out an enum *constant* by name, not the group:
            # `from com.sun.star.style import BreakType` fails with "No
            # module named 'com'", where importing the constant works.
            wanted = BREAKS[kind]
            module = __import__("com.sun.star.style.BreakType", globals(),
                                locals(), [wanted])
            paragraph.BreakType = getattr(module, wanted)
            if page_style is not None:
                paragraph.PageDescName = page_style
            elif kind == "none":
                # Measured: writing None here throws, and "" is what clears
                # it — the property then reads back as None.
                paragraph.PageDescName = ""
            if page_number is not None:
                paragraph.PageNumberOffset = page_number
            break_now = _get_property(paragraph, "BreakType", None)
            return {"paragraph": index, "kind": kind,
                    "break_type": getattr(break_now, "value", None),
                    "page_style": _get_property(paragraph, "PageDescName",
                                                None),
                    "page_number": _get_property(paragraph,
                                                 "PageNumberOffset", None)}

        return self._guarded_edit(doc, f"MCP: {kind.replace('_', ' ')}",
                                  track_changes, edit)

    def set_line_numbering(self, on: Optional[bool] = None,
                           interval: Optional[int] = None,
                           restart_each_page: Optional[bool] = None,
                           count_empty_lines: Optional[bool] = None,
                           distance_mm: Optional[float] = None,
                           track_changes: Optional[bool] = None,
                           doc: Any = None) -> Dict[str, Any]:
        """Number the lines of a document, or stop numbering them"""
        doc, error = self._writer_document(doc, "Numbering the lines")
        if error:
            return error
        if all(one is None for one in (on, interval, restart_each_page,
                                       count_empty_lines, distance_mm)):
            return refusal("INVALID_PARAMETER",
                           "say what to change: on, interval, "
                           "restart_each_page, count_empty_lines or "
                           "distance_mm")
        if interval is not None and (isinstance(interval, bool)
                                     or not isinstance(interval, int)
                                     or interval < 1):
            return refusal("INVALID_PARAMETER",
                           f"interval is how many lines between numbers, 1 or "
                           f"more, got {interval!r}")
        settings = _get_property(doc, "LineNumberingProperties", None)
        if settings is None:
            return refusal("UNSUPPORTED", "this document cannot number lines")
        was = self._line_numbering_of(doc)

        def edit():
            if interval is not None:
                settings.Interval = interval
            if restart_each_page is not None:
                settings.RestartAtEachPage = bool(restart_each_page)
            if count_empty_lines is not None:
                settings.CountEmptyLines = bool(count_empty_lines)
            if distance_mm is not None:
                settings.Distance = int(round(float(distance_mm) * 100))
            if on is not None:
                settings.IsOn = bool(on)
            now = self._line_numbering_of(doc)
            now["was"] = was
            return now

        return self._guarded_edit(doc, "MCP: line numbering", track_changes,
                                  edit)
