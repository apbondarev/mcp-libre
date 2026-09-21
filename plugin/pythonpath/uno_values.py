"""What a UNO value is, and what it means.

The constants every part of the bridge shares, and the small functions that
turn a UNO value into something a caller can read — a colour into "#RRGGBB",
a locale into "ru-RU", a pyuno enum into its name. They know nothing about
documents, so nothing here imports from the rest of the bridge.
"""

import uno
from typing import Any, Dict, List, Optional
from pathlib import Path
from urllib.parse import quote, unquote, urlparse
from urllib.request import url2pathname
import os
import datetime
import logging
import re

logger = logging.getLogger(__name__)

# A UNO document proxy is <class 'pyuno'> and inherits none of the
# com.sun.star.* interfaces, so isinstance() against them is always False.
# Service names are the only working way to tell document types apart.
WRITER_SERVICE = "com.sun.star.text.TextDocument"
CALC_SERVICE = "com.sun.star.sheet.SpreadsheetDocument"
IMPRESS_SERVICE = "com.sun.star.presentation.PresentationDocument"
DRAW_SERVICE = "com.sun.star.drawing.DrawingDocument"
DOCUMENT_SERVICES = (WRITER_SERVICE, CALC_SERVICE, IMPRESS_SERVICE, DRAW_SERVICE)


def _supports(obj: Any, service: str) -> bool:
    """Whether a UNO object implements a service, False if it cannot be asked"""
    try:
        return bool(obj.supportsService(service))
    except Exception as e:
        # Not silent: a component that cannot answer used to be reported as
        # "not a Writer document", which sent debugging in the wrong direction.
        logger.debug(f"Could not ask for {service}: {e}")
        return False


LANGUAGE_TAG = re.compile(r"^([A-Za-z]{2,3})(?:[-_]([A-Za-z]{2}))?$")

# Words for spell checking: letters, with apostrophes and hyphens inside a word
# but never at its edge, and no digits — "3.14" and "42" are not spellable.
WORD = re.compile(r"[^\W\d_]+(?:['\u2019-][^\W\d_]+)*")

# Caps for check_spelling
DEFAULT_SPELLING_RESULTS = 50
MAX_SPELLING_RESULTS = 200


def _locale(language: str) -> Any:
    """
    Turn a language tag such as "ru-RU" into com.sun.star.lang.Locale

    Raises AddressError — the caller already turns that into a refusal — when
    the tag is not one, rather than silently marking text as some other
    language.
    """
    match = LANGUAGE_TAG.match(language) if isinstance(language, str) else None
    if not match:
        raise AddressError(
            f"language must be a tag like \"ru-RU\" or \"en\", got {language!r}")

    locale = uno.createUnoStruct("com.sun.star.lang.Locale")
    locale.Language = match.group(1).lower()
    locale.Country = (match.group(2) or "").upper()
    locale.Variant = ""
    return locale


def _locale_name(locale: Any) -> Optional[str]:
    """"ru-RU" for a Locale, None when it carries no language"""
    language = _get_property(locale, "Language", "") or ""
    if not language:
        return None
    return f"{language}-{_get_property(locale, 'Country', '') or ''}"


COLOUR_TAG = re.compile(r"^#?([0-9A-Fa-f]{6})$")

# What gives a hyperlink its look. The navy underline is these character
# styles, not a colour anyone set, so restoring a link means restoring them.
UNVISITED_LINK_STYLE = "Internet link"
VISITED_LINK_STYLE = "Visited Internet Link"

# 1 point in 1/100 mm, the unit UNO uses for widths and distances
HUNDREDTHS_MM_PER_POINT = 2540.0 / 72.0


# What Writer calls automatic: no colour of one's own, so the text takes the
# colour of its style and the background stays transparent. Measured: -1 is
# exactly what untouched text reports for CharColor and CharBackColor alike,
# and writing it back is how a colour someone else applied is taken off —
# black is not the same thing, it is a colour.
AUTOMATIC_COLOUR = -1
AUTOMATIC_NAMES = ("auto", "automatic", "none", "default")


def _colour(value: Any) -> int:
    """
    A colour as UNO wants it: 0xRRGGBB, or -1 for automatic

    Accepts "#F5F5F5", "F5F5F5", a plain integer, and "auto"/"automatic" for
    the colour a style decides. Anything else is refused rather than painting
    text something arbitrary.
    """
    if isinstance(value, bool):
        raise AddressError(f"colour must be #RRGGBB or a number, got {value!r}")
    if isinstance(value, str) and value.strip().lower() in AUTOMATIC_NAMES:
        return AUTOMATIC_COLOUR
    if isinstance(value, int):
        if value == AUTOMATIC_COLOUR or 0 <= value <= 0xFFFFFF:
            return value
        raise AddressError(f"colour {value} is outside 0x000000..0xFFFFFF")
    match = COLOUR_TAG.match(value) if isinstance(value, str) else None
    if not match:
        raise AddressError(
            f"colour must look like \"#RRGGBB\", or \"automatic\" to take a "
            f"colour off, got {value!r}")
    return int(match.group(1), 16)


def _colour_name(value: int) -> str:
    """The #RRGGBB spelling of a colour, or "automatic" for no colour at all"""
    if value == AUTOMATIC_COLOUR:
        return "automatic"
    return f"#{value:06X}"


def _points_to_uno(points: float) -> int:
    """Points to the 1/100 mm UNO measures widths and distances in"""
    return int(round(float(points) * HUNDREDTHS_MM_PER_POINT))


def _border_line(colour: int, points: float) -> Any:
    """A com.sun.star.table.BorderLine2 of the given colour and thickness"""
    line = uno.createUnoStruct("com.sun.star.table.BorderLine2")
    width = _points_to_uno(points)
    line.Color = colour
    line.LineStyle = 0  # SOLID
    line.LineWidth = width
    line.OuterLineWidth = width
    line.InnerLineWidth = 0
    line.LineDistance = 0
    return line


ANNOTATION_SERVICE = "com.sun.star.text.textfield.Annotation"


def _comment_key(comment: Dict[str, Any]) -> tuple:
    """What makes two comment descriptions the same comment"""
    return (comment.get("author", "") or "", comment.get("content", "") or "",
            bool(comment.get("resolved")))


def _distinct_images(runs: Any) -> list:
    """The pictures anchored across a stretch of runs, each counted once"""
    found = []
    seen = set()
    for run in runs:
        for image in run.get("images", []) or []:
            name = image.get("name") or id(image)
            if name in seen:
                continue
            seen.add(name)
            found.append(image)
    return found


# What Writer calls a recorded change, in words a caller can read.
REDLINE_KINDS = {
    "Insert": "insert",
    "Delete": "delete",
    "Format": "format",
    "ParagraphFormat": "paragraph format",
    "TableInsert": "table inserted",
    "TableDelete": "table deleted",
    "TableCellInsert": "cell inserted",
    "TableCellDelete": "cell deleted",
}


def _distinct_changes(runs: Any) -> list:
    """The recorded changes covering a stretch of runs, each counted once.

    A change marks its text the way a comment does — empty marker portions
    around it — so one change over three runs is reported on each of them.
    """
    found = []
    seen = set()
    for run in runs:
        for change in run.get("changes", []) or []:
            marker = change.get("id") or id(change)
            if marker in seen:
                continue
            seen.add(marker)
            found.append(change)
    return found


def _distinct_comments(runs: Any) -> list:
    """
    The comments covering a stretch of runs, each counted once

    read_runs reports a comment on every run its anchor covers, so summing
    the per-run lists counted a comment spanning three runs three times —
    which turned up as "3 comments" in a refusal about one.
    """
    found = []
    seen = set()
    for run in runs:
        for note in run.get("comments", []) or []:
            marker = id(note)
            if marker in seen:
                continue
            seen.add(marker)
            found.append(note)
    return found


def _raw(value: Any) -> Any:
    """The value as JSON can carry it, for a caller that wants the number"""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    enum = getattr(value, "value", None)
    if isinstance(enum, str):
        return enum
    # The structs a style actually carries, as fields rather than as the
    # repr of a pyuno struct, which no caller can do anything with.
    mode = _get_property(value, "Mode", None)
    height = _get_property(value, "Height", None)
    if mode is not None and height is not None:
        return {"mode": LINE_SPACING_MODES.get(mode, mode), "height": height}
    width = _get_property(value, "LineWidth", None)
    if width is not None:
        return {"line_width": width,
                "color": _get_property(value, "Color", None),
                "line_style": _raw(_get_property(value, "LineStyle", None))}
    language = _get_property(value, "Language", None)
    if language is not None:
        return _locale_name(value)
    return str(value)


def _property_state_name(state: Any) -> Optional[str]:
    """"DIRECT_VALUE" for a com.sun.star.beans.PropertyState"""
    if state is None:
        return None
    return getattr(state, "value", None) or str(state)


def _get_property_state(source: Any, name: str) -> Optional[str]:
    """Whether `source` sets `name` itself, inherits it, or cannot say"""
    try:
        return _property_state_name(source.getPropertyState(name))
    except Exception:
        return None


def _style_value(name: str, value: Any) -> Any:
    """
    A style property as a person would read it

    Writer keeps lengths in 1/100 mm, weights as a number where 150 is bold,
    colours as a signed integer and enums as pyuno wrappers, so the raw value
    is kept alongside anything translated.
    """
    if value is None:
        return None
    enum = getattr(value, "value", None)
    if isinstance(enum, str):                      # a pyuno enum
        return enum
    if name in STYLE_HUNDREDTHS_MM and isinstance(value, (int, float)):
        return f"{round(value / 100.0, 2)} mm"
    if name in STYLE_POINTS and isinstance(value, (int, float)):
        return f"{round(float(value), 1)} pt"
    if name in STYLE_COLOURS and isinstance(value, int):
        return "automatic" if value == -1 else _colour_name(value & 0xFFFFFF)
    if name.startswith("CharWeight") and isinstance(value, (int, float)):
        return "bold" if value > 120 else "normal"
    if name == "ParaAdjust" and isinstance(value, int):
        return PARAGRAPH_ADJUST.get(value, value)
    if name == "Category" and isinstance(value, int):
        return STYLE_CATEGORIES.get(value, value)
    if name.startswith("CharLocale"):
        return _locale_name(value)
    if name == "Size" and hasattr(value, "Width"):
        # The page's own size, which stringifies as a struct dump otherwise.
        return (f"{round(_get_property(value, 'Width', 0) / 100.0, 2)} × "
                f"{round(_get_property(value, 'Height', 0) / 100.0, 2)} mm")
    if name == "TextColumns" and hasattr(value, "getColumnCount"):
        # Measured: a style with no columns of its own counts 0, and so does
        # one told to use a single column.
        try:
            return max(1, value.getColumnCount())
        except Exception:
            return None
    if name == "ParaLineSpacing":
        mode = _get_property(value, "Mode", None)
        height = _get_property(value, "Height", None)
        if mode == 0:
            return f"{height}%"
        if isinstance(height, (int, float)):
            return (f"{LINE_SPACING_MODES.get(mode, mode)} "
                    f"{round(height / 100.0, 2)} mm")
        return f"{LINE_SPACING_MODES.get(mode, mode)}"
    if name.endswith("Border"):
        width = _get_property(value, "LineWidth", None)
        if not width:
            return "none"
        colour = _get_property(value, "Color", 0) or 0
        return (f"{round(width / 100.0, 2)} mm, "
                f"{_colour_name(colour & 0xFFFFFF)}")
    if isinstance(value, (str, int, float, bool)):
        return value
    # A struct or something else pyuno will not simplify: say what it is.
    return str(value)


def _get_document_url(doc: Any) -> str:
    """The document's URL, or "" for one that has never been saved"""
    try:
        return doc.getURL() or ""
    except Exception:
        return ""


def _document_path(doc: Any) -> Optional[str]:
    """The document's file path, or None when it lives nowhere yet"""
    url = _get_document_url(doc)
    if not url.startswith("file://"):
        return None
    # url2pathname gives 'C:\Users\x' for 'file:///C:/Users/x' on Windows and
    # the plain unquoted path elsewhere.
    return url2pathname(urlparse(url).path)


def _get_property_call(source: Any, method: str, default: Any = None) -> Any:
    """The result of a no-argument method, or `default` if it will not answer"""
    try:
        return getattr(source, method)()
    except Exception as e:
        logger.info(f"Could not call {method}: {e}")
        return default


def _file_url(path: str) -> str:
    """A file:// URL UNO accepts, with the odd character in a name escaped"""
    # as_uri is 'file:///C:/Users/x' on Windows, where 'file://' + a path
    # would give 'file://C%3A%5C…' — and 'file:///home/x' elsewhere.
    return Path(os.path.abspath(path)).as_uri()


def _anchor_kind(anchor_type: Any) -> Optional[str]:
    """"AS_CHARACTER" for a pyuno TextContentAnchorType enum"""
    if anchor_type is None:
        return None
    return getattr(anchor_type, "value", None) or str(anchor_type)


def _same_paragraph(first: Any, second: Any) -> bool:
    """
    Whether two proxies are the same paragraph

    pyuno mints a fresh proxy per call, so `is` is never the answer; the text
    they belong to can compare their starts.
    """
    try:
        return first.getText().compareRegionStarts(first.getStart(),
                                                   second.getStart()) == 0
    except Exception:
        return False


def _column_letters(index: int) -> str:
    """0 -> "A", 25 -> "Z", 26 -> "AA", as a table names its columns"""
    letters = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


def _cell_position(cell_name: str) -> tuple:
    """(row, column) counting from 1 for a cell called "B3", or (None, None)"""
    match = re.match(r"^([A-Z]+)(\d+)$", (cell_name or "").upper())
    if not match:
        return None, None
    column = 0
    for letter in match.group(1):
        column = column * 26 + (ord(letter) - ord("A") + 1)
    return int(match.group(2)), column


def _column_shares(table: Any) -> Optional[List[float]]:
    """
    What share of the table each column takes, as percentages

    The separators are positions on a scale whose end is
    TableColumnRelativeSum, so this is the one width a caller can rely on —
    Width itself is on a scale of Writer's own.
    """
    try:
        separators = _get_property(table, "TableColumnSeparators", None) or ()
        total = _get_property(table, "TableColumnRelativeSum", 0) or 10000
        positions = [0]
        for separator in separators:
            position = _get_property(separator, "Position", None)
            if position is None:
                return None
            positions.append(position)
        positions.append(total)
        return [round((end - start) * 100.0 / total, 1)
                for start, end in zip(positions, positions[1:])]
    except Exception as e:
        logger.info(f"Could not read the column widths: {e}")
        return None


def _table_size(table: Any) -> tuple:
    """(rows, columns) of a table, or (None, None) if it will not say"""
    rows = columns = None
    try:
        rows = table.getRows().getCount()
    except Exception as e:
        logger.info(f"Could not count the rows: {e}")
    try:
        columns = table.getColumns().getCount()
    except Exception as e:
        logger.info(f"Could not count the columns: {e}")
    return rows, columns


def _millimetres(hundredths: Any) -> Optional[float]:
    """Writer measures a frame in 1/100 mm; people do not"""
    if not isinstance(hundredths, (int, float)):
        return None
    return round(hundredths / 100.0, 1)


def _comment_date(note: Any) -> Optional[str]:
    """The comment's timestamp as ISO 8601, from the DateTimeValue struct"""
    stamp = _get_property(note, "DateTimeValue", None)
    if stamp is None or not getattr(stamp, "Year", 0):
        return None                      # Writer leaves it zeroed until set
    try:
        return (f"{stamp.Year:04d}-{stamp.Month:02d}-{stamp.Day:02d}"
                f"T{stamp.Hours:02d}:{stamp.Minutes:02d}:{stamp.Seconds:02d}")
    except Exception as e:
        logger.info(f"Could not read a comment's date: {e}")
        return None


def _stamp_of(carrier: Any, name: str) -> Optional[str]:
    """A UNO DateTime property as ISO 8601, or None when it is zeroed."""
    stamp = _get_property(carrier, name, None)
    if stamp is None or not getattr(stamp, "Year", 0):
        return None
    try:
        return (f"{stamp.Year:04d}-{stamp.Month:02d}-{stamp.Day:02d}"
                f"T{stamp.Hours:02d}:{stamp.Minutes:02d}:{stamp.Seconds:02d}")
    except Exception as e:
        logger.info(f"Could not read {name}: {e}")
        return None


def _comment_language(note: Any) -> Optional[str]:
    """
    The language of a comment's own text

    Writer spell checks the note in the margin against this, so a Russian
    comment left at en-US is underlined word by word. It lives on the runs
    of the annotation's own text, not on the field.
    """
    body = _get_property(note, "TextRange", None)
    if body is None:
        return None
    try:
        paragraphs = body.createEnumeration()
        while paragraphs.hasMoreElements():
            portions = paragraphs.nextElement().createEnumeration()
            while portions.hasMoreElements():
                portion = portions.nextElement()
                if portion.getString():
                    return _locale_name(_get_property(portion, "CharLocale",
                                                      None))
    except Exception as e:
        logger.info(f"Could not read a comment's language: {e}")
    return _locale_name(_get_property(body, "CharLocale", None))


# How the language of a comment's text is set, all of it measured on a live
# LibreOffice because none of it is guessable:
#
#   * Not on the annotation. Its TextRange hands out a detached copy: a
#     locale written through a cursor over it reads back from that cursor,
#     is gone from the next enumeration, and leaves no trace in content.xml.
#   * The "Comment" paragraph style is where it comes from, and a note takes
#     it when the note is *created* — not when the style changes. Setting the
#     style therefore leaves every existing note exactly as it was.
#   * Writing a note's text again with the style set does not restamp it
#     either: tried, and the note kept its old language. So the language of a
#     comment already in the document can only be changed by making the
#     comment again, which is what update_comment does — and says.
#   * Setting the style for the moment of creation and putting it back
#     afterwards does *not* work: the note then reports the language the
#     style was put back to. The style has to stay set. So asking for a
#     comment in a language sets the language of the document's comments,
#     and every tool that takes one says so in its result.
COMMENT_STYLE = "Comment"

GRAPHIC_SERVICE = "com.sun.star.text.TextGraphicObject"

# What an image is to the text it sits in, all measured on a live LibreOffice:
#
#   * A picture is an empty portion of type "Frame" at its anchor offset, for
#     both anchor kinds, and it adds no characters — so reading runs skipped
#     it as an empty portion and nothing reported it at all.
#   * Replacing a stretch that covers an AS_CHARACTER (inline) picture
#     destroys it. An AT_CHARACTER one survives, but its anchor jumps to the
#     start of the replaced stretch.
#   * A picture can be put back from its own Graphic, keeping its size, its
#     title and its alternative text.
INLINE_ANCHOR = "AS_CHARACTER"

# What GraphicProvider will write. "original" asks for the picture's own type.
IMAGE_TYPES = {"png": "image/png", "jpeg": "image/jpeg", "jpg": "image/jpeg",
               "gif": "image/gif", "tiff": "image/tiff", "bmp": "image/bmp"}
IMAGE_EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif",
                    "image/tiff": "tif", "image/bmp": "bmp",
                    "image/svg+xml": "svg"}
MAX_INLINE_IMAGE_BYTES = 4 * 1024 * 1024

# Rendering a page, with nothing but LibreOffice — no poppler, no
# ImageMagick, no screenshot tool. Measured:
#
#   * The filter "writer_png_Export" writes a PNG of ONE page, at the pixel
#     size given in its FilterData, and it renders the page the view is on:
#     PageRange and friends in the FilterData are ignored, but jumping the
#     view cursor to a page changes what comes out. That is the fast path.
#   * As a fallback, the page exports to PDF (where PageRange *is* honoured),
#     the PDF loads into a hidden Draw document through "draw_pdf_import",
#     and "draw_png_Export" writes the picture. Also LibreOffice alone.
#   * A page rendered this way is the page as it prints. The spell checker's
#     red underlines, the caret and the text boundary marks live in Writer's
#     window, not in the page, and no export shows them.
# Describing a style. What a caller wants to know is not the 195 properties
# a paragraph style carries but the handful it sets *itself* — and UNO says
# which those are: getPropertyState is DIRECT_VALUE for a property the style
# defines and DEFAULT_VALUE for one it inherits. For "Text body" that is 8 of
# 195, and they match its definition in styles.xml exactly, which is why
# nobody has to read the file to find out.
# Saving under a name. storeAsURL with no FilterName writes ODF whatever the
# file is called — measured: a document saved as "x.docx" came out as ODF
# with a .docx name, which Word opens only under protest. So the format is
# chosen from the extension, or said outright, and an extension nobody knows
# is refused rather than silently written as ODF.
TABLE_SERVICE = "com.sun.star.text.TextTable"
CELL_SERVICE = "com.sun.star.text.CellProperties"

# Formatting a table, all of it measured:
#
#   * The grid comes from the TableBorder2 struct — TopLine, BottomLine,
#     LeftLine, RightLine, HorizontalLine, VerticalLine — and the Is*Valid
#     flags beside them must be set or the change is dropped on the floor.
#     Distance in the same struct is the padding inside every cell.
#   * A cell's background is BackColor with BackTransparent turned off. A
#     cell has no FillStyle and no FillColor at all, unlike a paragraph,
#     where ParaBackColor is unwritable and FillStyle is the way in.
#   * Column widths are the separator positions against
#     TableColumnRelativeSum, and they come back rounded (3000 reads as
#     2999), so they are never compared for equality.
#   * A cell is an XText: its paragraphs take ParaStyleName, and a cursor
#     over it takes character formatting like any other range.

# A selection that runs from text through a table comes back as ONE range
# over the body text, with the cells' text folded into its string by
# newlines: 318 characters where the paragraph holds 206. So the string says
# nothing about the table, _locate_range hands back a body address whose
# length runs past the paragraph, and setString over that range DESTROYS the
# table — measured: one table in, none out, everything collapsed into a
# single paragraph. The paragraph-scoped flatten guard cannot see that, so a
# range is asked separately what it spans.

# A table's Width is NOT in 1/100 mm, whatever it looks like: a default table
# spanning a 170.01 mm text area reports 115596, and the same number came back
# from a table in a real document — it is Writer's own scale, so translating
# it to millimetres invented 1.16 metres. What is measurable is the share of
# each column: TableColumnSeparators are positions against
# TableColumnRelativeSum (10000), and for three equal columns they came back
# at 3333 and 6666, which is the truth a caller can use.
#
# Where the caret is, when it is in a table, needs no searching: the view
# cursor carries the table in TextTable and the cell in Cell, and the cell
# names itself ("A1"). A cell is its own XText — which is why a caret in one
# reports no body paragraph, and why every text tool says "outside the body
# text" there. Cell text is read with getString() per cell rather than
# getDataArray(), which turns a number into a float and flattens the
# paragraphs of a cell into one line.

WRITER_SAVE_FILTERS = {"odt": "writer8", "docx": "MS Word 2007 XML",
                       "doc": "MS Word 97", "rtf": "Rich Text Format",
                       "txt": "Text", "html": "HTML (StarWriter)",
                       "xhtml": "XHTML Writer File", "fodt": "OpenDocument Text Flat XML"}
# PDF is an export, not a place a document can live: storeAsURL would leave
# the document claiming to be one.
EXPORT_ONLY_FORMATS = {"pdf", "epub", "png", "jpg", "jpeg"}

STYLE_FAMILIES = {"paragraph": "ParagraphStyles", "character": "CharacterStyles",
                  "page": "PageStyles", "frame": "FrameStyles",
                  "numbering": "NumberingStyles", "table": "TableStyles",
                  "cell": "CellStyles"}

# Writer measures these in 1/100 mm, and font sizes in points.
STYLE_HUNDREDTHS_MM = {
    "Width", "Height", "TopMargin", "BottomMargin", "LeftMargin",
    "RightMargin", "GutterMargin",
    "ParaTopMargin", "ParaBottomMargin", "ParaLeftMargin", "ParaRightMargin",
    "ParaFirstLineIndent", "TopBorderDistance", "BottomBorderDistance",
    "LeftBorderDistance", "RightBorderDistance", "BorderDistance",
    "ParaLineSpacingFix", "Width", "Height", "LeftMargin", "RightMargin",
    "TopMargin", "BottomMargin", "ParaTabStopDefaultDistance"}
STYLE_POINTS = {"CharHeight", "CharHeightAsian", "CharHeightComplex"}
STYLE_COLOURS = {"CharColor", "CharBackColor", "ParaBackColor", "FillColor",
                 "CharUnderlineColor", "BackColor"}

PARAGRAPH_ADJUST = {0: "left", 1: "right", 2: "justified", 3: "centred",
                    4: "stretched"}
LINE_SPACING_MODES = {0: "proportional", 1: "at least", 2: "leading",
                      3: "exactly"}
STYLE_CATEGORIES = {0: "text", 1: "chapter", 2: "list", 3: "index",
                    4: "extra", 5: "html"}

# What "everything about this style" means in practice, in the order a reader
# would want it. Anything else is reachable with all_properties=true.
# What is in force on a **page** style is a different question from what is
# in force on a paragraph: a page has a size, margins and columns, and none
# of the character properties mean anything on one.
STYLE_EFFECTIVE_PAGE = (
    "Width", "Height", "IsLandscape", "TopMargin", "BottomMargin",
    "LeftMargin", "RightMargin", "GutterMargin", "TextColumns",
    "NumberingType", "PageStyleLayout", "FollowStyle", "HeaderIsOn",
    "FooterIsOn", "BackColor", "FillStyle", "FillColor")

STYLE_EFFECTIVE = (
    "CharFontName", "CharHeight", "CharWeight", "CharPosture",
    "CharUnderline", "CharColor", "CharBackColor", "CharLocale",
    "ParaAdjust", "ParaLineSpacing", "ParaTopMargin", "ParaBottomMargin",
    "ParaLeftMargin", "ParaRightMargin", "ParaFirstLineIndent",
    "ParaContextMargin", "ParaKeepTogether", "ParaSplit", "ParaOrphans",
    "ParaWidows", "ParaBackColor", "FillStyle", "FillColor", "TopBorder",
    "BottomBorder", "LeftBorder", "RightBorder", "NumberingStyleName",
    "OutlineLevel", "PageDescName", "BreakType")

MIN_RENDER_DPI, MAX_RENDER_DPI = 20, 300
MAX_RENDER_PIXELS = 5000


def _write_comment_text(note: Any, text: str):
    """Put `text` in the note; Writer ignores a write of the same string"""
    if text != (_get_property(note, "Content", "") or ""):
        note.Content = text


def _stamp_comment(note: Any):
    """
    Date a comment, the way Writer dates the ones made in its interface

    An annotation created through the API carries a zeroed DateTimeValue —
    measured — so the comment shows up in the margin with no date until it
    is set.
    """
    try:
        if getattr(_get_property(note, "DateTimeValue", None), "Year", 0):
            return
        now = datetime.datetime.now()
        stamp = uno.createUnoStruct("com.sun.star.util.DateTime")
        stamp.Year, stamp.Month, stamp.Day = now.year, now.month, now.day
        stamp.Hours, stamp.Minutes, stamp.Seconds = (now.hour, now.minute,
                                                     now.second)
        stamp.NanoSeconds = 0
        stamp.IsUTC = False
        note.DateTimeValue = stamp
    except Exception as e:
        logger.info(f"Could not date a comment: {e}")


def _describe_comment(note: Any) -> Dict[str, Any]:
    """
    A comment as a caller sees it

    `id` is the annotation's own Name, which Writer mints per comment. It is
    the only stable way to name one for editing or deleting: author, text and
    anchor can all coincide. It does not survive a rewrite that re-anchors the
    comment — replace_runs creates a new annotation, hence a new id.
    """
    return {
        "id": _get_property(note, "Name", "") or "",
        "author": _get_property(note, "Author", "") or "",
        "content": _get_property(note, "Content", "") or "",
        "resolved": bool(_get_property(note, "Resolved", False)),
        "initials": _get_property(note, "Initials", "") or "",
        "language": _comment_language(note),
        "date": _comment_date(note),
        "reply_to": _get_property(note, "ParentName", "") or None
    }


def _is_italic(posture: Any) -> bool:
    """
    Whether a CharPosture means italic

    A pyuno enum stringifies as "<Enum instance com.sun.star.awt.FontSlant
    ('ITALIC')>", so testing str(...) for a suffix reported every run as
    upright — caught by the live harness after the fakes, which hand back a
    plain string, had passed. The enum's own value is the thing to read.
    """
    if posture is None:
        return False
    name = getattr(posture, "value", None) or str(posture)
    return "ITALIC" in name


def _is_readonly(doc: Any) -> bool:
    """Whether the document refuses edits, False when it cannot be asked"""
    try:
        return bool(doc.isReadonly())
    except Exception as e:
        logger.debug(f"Could not ask whether the document is read-only: {e}")
        return False


def _is_document(component: Any) -> bool:
    """Whether a component is a document, rather than a dialog or the Start Center"""
    return any(_supports(component, service) for service in DOCUMENT_SERVICES)


# Why a tool said no, as a word a caller can branch on instead of matching
# on English. Kept small on purpose: a code earns its place only when a
# caller would do something different about it.
#
#   NO_DOCUMENT           nothing to act on — no document, or none open
#   WRONG_DOCUMENT_TYPE   a Writer tool pointed at Calc, Draw, a dialog
#   READ_ONLY             the document refuses every edit
#   INVALID_ADDRESS       the address does not resolve here and now
#   NOT_FOUND             a thing named by the caller is not in the document
#   INVALID_PARAMETER     an argument is missing, of the wrong kind or range
#   WOULD_LOSE_FORMATTING the write was refused because it would destroy runs,
#                         links, comments, pictures or a table
#   UNSUPPORTED           LibreOffice here cannot do it at all
#   FAILED                UNO said no, or nothing better is known
ERROR_CODES = frozenset({
    "NO_DOCUMENT", "WRONG_DOCUMENT_TYPE", "READ_ONLY", "INVALID_ADDRESS",
    "NOT_FOUND", "INVALID_PARAMETER", "WOULD_LOSE_FORMATTING", "UNSUPPORTED",
    "FAILED",
})


def refusal(code, message, **extra):
    """A refusal a caller can act on: the reason in English and in a word."""
    if code not in ERROR_CODES:
        raise ValueError(f"unknown error code {code!r}; "
                         f"known: {', '.join(sorted(ERROR_CODES))}")
    answer = {"success": False, "error": str(message), "code": code}
    answer.update(extra)
    return answer


class AddressError(Exception):
    """An address that cannot be resolved to a text range"""


# Cap on paragraph and selection text returned by get_cursor_info; a single
# paragraph (or a select-all) can otherwise be megabytes of MCP payload.
MAX_TEXT_CHARS = 2000

# Paragraph window sizes for read_paragraphs
DEFAULT_PARAGRAPH_COUNT = 50
# How many anchors a session keeps. They are a way through one piece of work,
# not a mark in the file, so the oldest is let go when a new one is made past
# this. It also bounds what a single read may hand out: a read of more
# paragraphs than this is refused with anchors on, rather than answered with
# tokens that were let go while the answer was being built.
MAX_ANCHORS = 2000

# What one read carries when nobody says otherwise is DEFAULT_PARAGRAPH_COUNT
# above; this is no longer a ceiling, only the window a caller is wise to page
# by over a socket. A caller who asks for a whole document gets it.
MAX_PARAGRAPH_COUNT = 200

# How many paragraphs' runs one read_runs call will read. The runs of a
# paragraph are several objects each, so a block of a hundred is already a
# large answer; beyond this the call says it stopped rather than growing
# without bound.
MAX_RUN_PARAGRAPHS = 50

# How many headings get_outline returns when nobody says. It is a default,
# not a ceiling: an outline entry is a line, not a paragraph of text, and a
# caller asking for a whole map — 938 headings on a real guide — should get
# it in one call rather than being told the answer stops at 200.
DEFAULT_OUTLINE_ENTRIES = 200
MAX_OUTLINE_ENTRIES = DEFAULT_OUTLINE_ENTRIES   # the old name, kept

# Search result caps for find_text
DEFAULT_SEARCH_RESULTS = 50
MAX_SEARCH_RESULTS = 200


def _get_property(obj: Any, name: str, default: Any = None) -> Any:
    """Read a UNO property, falling back when the object does not carry it"""
    try:
        return getattr(obj, name)
    except Exception:
        return default


def _heading_level(paragraph: Any) -> int:
    """
    Outline level of a paragraph, 0 when it is body text

    OutlineLevel covers custom styles that were given a level; the style-name
    check is the fallback for builds that do not expose the property.
    """
    level = _get_property(paragraph, "OutlineLevel", 0)
    if isinstance(level, int) and not isinstance(level, bool) and level > 0:
        return level

    style = _get_property(paragraph, "ParaStyleName", "") or ""
    if style.startswith("Heading "):
        suffix = style[len("Heading "):].strip()
        if suffix.isdigit():
            return int(suffix)
    return 0


def _text_payload(value: str) -> Dict[str, Any]:
    """Cap text at MAX_TEXT_CHARS while still reporting its true length"""
    return {
        "text": value[:MAX_TEXT_CHARS],
        "length": len(value),
        "truncated": len(value) > MAX_TEXT_CHARS,
    }
