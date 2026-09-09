"""
LibreOffice MCP Extension - UNO Bridge Module

This module provides a bridge between MCP operations and LibreOffice UNO API,
enabling direct manipulation of LibreOffice documents.
"""

import uno
import unohelper
from com.sun.star.beans import PropertyValue
from com.sun.star.document import XDocumentEventListener
from com.sun.star.awt import XActionListener
from typing import Any, Optional, Dict, List
from urllib.parse import quote
import base64
import tempfile
import os
import datetime
import uuid
import logging
import re
import traceback

# Set up logging
logging.basicConfig(level=logging.INFO)
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


def _colour(value: Any) -> int:
    """
    A colour as UNO wants it: 0xRRGGBB

    Accepts "#F5F5F5", "F5F5F5" or a plain integer, and refuses anything else
    rather than painting text some arbitrary colour.
    """
    if isinstance(value, bool):
        raise AddressError(f"colour must be #RRGGBB or a number, got {value!r}")
    if isinstance(value, int):
        if 0 <= value <= 0xFFFFFF:
            return value
        raise AddressError(f"colour {value} is outside 0x000000..0xFFFFFF")
    match = COLOUR_TAG.match(value) if isinstance(value, str) else None
    if not match:
        raise AddressError(
            f"colour must look like \"#RRGGBB\", got {value!r}")
    return int(match.group(1), 16)


def _colour_name(value: int) -> str:
    """The #RRGGBB spelling of a colour, for reporting back"""
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


def _file_url(path: str) -> str:
    """A file:// URL UNO accepts, with the odd character in a name escaped"""
    return "file://" + quote(os.path.abspath(path))


def _anchor_kind(anchor_type: Any) -> Optional[str]:
    """"AS_CHARACTER" for a pyuno TextContentAnchorType enum"""
    if anchor_type is None:
        return None
    return getattr(anchor_type, "value", None) or str(anchor_type)


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


class AddressError(Exception):
    """An address that cannot be resolved to a text range"""


# Cap on paragraph and selection text returned by get_cursor_info; a single
# paragraph (or a select-all) can otherwise be megabytes of MCP payload.
MAX_TEXT_CHARS = 2000

# Paragraph window sizes for read_paragraphs
DEFAULT_PARAGRAPH_COUNT = 50
MAX_PARAGRAPH_COUNT = 200

# Cap on headings returned by get_outline
MAX_OUTLINE_ENTRIES = 200

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


class UNOBridge:
    """Bridge between MCP operations and LibreOffice UNO API"""
    
    def __init__(self):
        """Initialize the UNO bridge"""
        try:
            self.ctx = uno.getComponentContext()
            self.smgr = self.ctx.ServiceManager
            self.desktop = self.smgr.createInstanceWithContext(
                "com.sun.star.frame.Desktop", self.ctx)
            logger.info("UNO Bridge initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize UNO Bridge: {e}")
            raise
    
    def create_document(self, doc_type: str = "writer") -> Any:
        """
        Create new document using UNO API
        
        Args:
            doc_type: Type of document ('writer', 'calc', 'impress', 'draw')
            
        Returns:
            Document object
        """
        try:
            url_map = {
                "writer": "private:factory/swriter",
                "calc": "private:factory/scalc", 
                "impress": "private:factory/simpress",
                "draw": "private:factory/sdraw"
            }
            
            url = url_map.get(doc_type, "private:factory/swriter")
            doc = self.desktop.loadComponentFromURL(url, "_blank", 0, ())
            logger.info(f"Created new {doc_type} document")
            return doc
            
        except Exception as e:
            logger.error(f"Failed to create document: {e}")
            raise
    
    def get_active_document(self) -> Optional[Any]:
        """
        The document to act on

        getCurrentComponent() follows the focused frame, and that is not always
        a document: with a modal dialog open — including this extension's own
        status box — or the Start Center focused, it answers with a component
        that supports no document service. Trusting it made every tool report
        "not a Writer document" while a Writer document was open, so an answer
        that is not a document falls back to the first document actually open.
        """
        try:
            current = self.desktop.getCurrentComponent()
            if _is_document(current):
                logger.info("Retrieved active document")
                return current

            if current is not None:
                logger.info("Current component is not a document (dialog or Start "
                            "Center?), falling back to an open document")
            return self._first_open_document()

        except Exception as e:
            logger.error(f"Failed to get active document: {e}")
            return None

    def open_documents(self) -> List[Any]:
        """Every open document, skipping dialogs and the Start Center"""
        documents = []
        try:
            enumeration = self.desktop.getComponents().createEnumeration()
        except Exception as e:
            logger.error(f"Could not enumerate open documents: {e}")
            return documents

        while enumeration.hasMoreElements():
            component = enumeration.nextElement()
            if _is_document(component):
                documents.append(component)
        return documents

    def _first_open_document(self) -> Optional[Any]:
        """The first open document, or None when nothing is open"""
        documents = self.open_documents()
        if not documents:
            logger.info("No open document found")
            return None
        return documents[0]
    
    def document_for(self, url: Optional[str] = None) -> Any:
        """
        The document a tool should act on

        Without a url this is the active document. With one it is the open
        document whose URL matches, so a tool never silently acts on whichever
        window happens to be focused. Returns None when nothing matches.
        """
        if not url:
            return self.get_active_document()

        try:
            enumeration = self.desktop.getComponents().createEnumeration()
        except Exception as e:
            logger.error(f"Could not enumerate open documents: {e}")
            return None

        while enumeration.hasMoreElements():
            component = enumeration.nextElement()
            try:
                if component.getURL() == url:
                    return component
            except Exception:
                continue  # not a document, e.g. the Start Center
        logger.info(f"No open document with URL {url}")
        return None

    def _writer_document(self, doc: Any, action: str) -> tuple:
        """
        (document, error) for a tool that needs a live Writer document

        Falls back to the active document and rejects anything that is not
        Writer, with an error naming what was being attempted.
        """
        if doc is None:
            doc = self.get_active_document()

        if not doc:
            return None, {"success": False, "error": "No document available"}

        if not _supports(doc, WRITER_SERVICE):
            return None, {
                "success": False,
                "error": f"{action} is only available for Writer documents, "
                         f"got {self._get_document_type(doc)}"
            }
        return doc, None

    def get_document_info(self, doc: Any = None) -> Dict[str, Any]:
        """Get information about a document"""
        try:
            if doc is None:
                doc = self.get_active_document()
            
            if not doc:
                return {"error": "No document available"}
            
            info = {
                "title": getattr(doc, 'Title', 'Unknown') if hasattr(doc, 'Title') else "Unknown",
                "url": doc.getURL() if hasattr(doc, 'getURL') else "",
                "modified": doc.isModified() if hasattr(doc, 'isModified') else False,
                "type": self._get_document_type(doc),
                "has_selection": self._has_selection(doc),
                # Whether edits are recorded decides how a replacement looks:
                # with recording on, the original stays struck through and the
                # new text is coloured, which reads as the edit having failed.
                "track_changes": bool(_get_property(doc, "RecordChanges", False)),
                "tracked_changes": self._count_tracked_changes(doc)
            }
            
            # Add document-specific information
            if _supports(doc, WRITER_SERVICE):
                text = doc.getText()
                info["word_count"] = len(text.getString().split())
                info["character_count"] = len(text.getString())
            elif _supports(doc, CALC_SERVICE):
                sheets = doc.getSheets()
                info["sheet_count"] = sheets.getCount()
                info["sheet_names"] = [sheets.getByIndex(i).getName() 
                                     for i in range(sheets.getCount())]
            
            return info
            
        except Exception as e:
            logger.error(f"Failed to get document info: {e}")
            return {"error": str(e)}
    
    def insert_text(self, text: str, position: Optional[int] = None, doc: Any = None) -> Dict[str, Any]:
        """
        Insert text into a document
        
        Args:
            text: Text to insert
            position: Position to insert at (None for current cursor position)
            doc: Document to insert into (None for active document)
            
        Returns:
            Result dictionary
        """
        try:
            if doc is None:
                doc = self.get_active_document()
            
            if not doc:
                return {"success": False, "error": "No active document"}
            
            # Handle Writer documents
            if _supports(doc, WRITER_SERVICE):
                text_obj = doc.getText()
                
                if position is None:
                    # Insert at current cursor position
                    cursor = doc.getCurrentController().getViewCursor()
                else:
                    # Insert at specific position
                    cursor = text_obj.createTextCursor()
                    cursor.gotoStart(False)
                    cursor.goRight(position, False)
                
                text_obj.insertString(cursor, text, False)
                logger.info(f"Inserted {len(text)} characters into Writer document")
                return {"success": True, "message": f"Inserted {len(text)} characters"}
            
            # Handle other document types
            else:
                return {"success": False, "error": f"Text insertion not supported for {self._get_document_type(doc)}"}
                
        except Exception as e:
            logger.error(f"Failed to insert text: {e}")
            return {"success": False, "error": str(e)}
    
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
                return {"success": False, "error": "No Writer document available"}
            
            # The selection, which must actually hold something: a collapsed
            # caret answers getCount() == 1 with an empty range, so the old
            # check never fired and this reported success while doing nothing.
            try:
                text_range = self._resolve_address(doc, {"selection": True})
            except AddressError as e:
                return {"success": False, "error": str(e)}

            if not text_range.getString():
                return {
                    "success": False,
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
            return {"success": False, "error": str(e)}
    
    def save_document(self, doc: Any = None, file_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Save a document
        
        Args:
            doc: Document to save (None for active document)
            file_path: Path to save to (None to save to current location)
            
        Returns:
            Result dictionary
        """
        try:
            if doc is None:
                doc = self.get_active_document()
            
            if not doc:
                return {"success": False, "error": "No document to save"}
            
            if file_path:
                # Save as new file
                url = uno.systemPathToFileUrl(file_path)
                doc.storeAsURL(url, ())
                logger.info(f"Saved document to {file_path}")
                return {"success": True, "message": f"Document saved to {file_path}"}
            else:
                # Save to current location
                if doc.hasLocation():
                    doc.store()
                    logger.info("Saved document to current location")
                    return {"success": True, "message": "Document saved"}
                else:
                    return {"success": False, "error": "Document has no location, specify file_path"}
                    
        except Exception as e:
            logger.error(f"Failed to save document: {e}")
            return {"success": False, "error": str(e)}
    
    def export_document(self, export_format: str, file_path: str, doc: Any = None) -> Dict[str, Any]:
        """
        Export document to different format
        
        Args:
            export_format: Target format ('pdf', 'docx', 'odt', 'txt', etc.)
            file_path: Path to export to
            doc: Document to export (None for active document)
            
        Returns:
            Result dictionary
        """
        try:
            if doc is None:
                doc = self.get_active_document()
            
            if not doc:
                return {"success": False, "error": "No document to export"}
            
            # Filter map for different formats
            filter_map = {
                'pdf': 'writer_pdf_Export',
                'docx': 'MS Word 2007 XML',
                'doc': 'MS Word 97',
                'odt': 'writer8',
                'txt': 'Text',
                'rtf': 'Rich Text Format',
                'html': 'HTML (StarWriter)'
            }
            
            filter_name = filter_map.get(export_format.lower())
            if not filter_name:
                return {"success": False, "error": f"Unsupported export format: {export_format}"}
            
            # Prepare export properties
            properties = (
                PropertyValue("FilterName", 0, filter_name, 0),
                PropertyValue("Overwrite", 0, True, 0),
            )
            
            # Export document
            url = uno.systemPathToFileUrl(file_path)
            doc.storeToURL(url, properties)
            
            logger.info(f"Exported document to {file_path} as {export_format}")
            return {"success": True, "message": f"Document exported to {file_path}"}
            
        except Exception as e:
            logger.error(f"Failed to export document: {e}")
            return {"success": False, "error": str(e)}
    
    def get_text_content(self, doc: Any = None) -> Dict[str, Any]:
        """Get text content from a document"""
        try:
            if doc is None:
                doc = self.get_active_document()
            
            if not doc:
                return {"success": False, "error": "No document available"}
            
            if _supports(doc, WRITER_SERVICE):
                text = doc.getText().getString()
                return {"success": True, "content": text, "length": len(text)}
            else:
                return {"success": False, "error": f"Text extraction not supported for {self._get_document_type(doc)}"}
                
        except Exception as e:
            logger.error(f"Failed to get text content: {e}")
            return {"success": False, "error": str(e)}
    
    def get_cursor_info(self, doc: Any = None) -> Dict[str, Any]:
        """
        Report where the caret is and what is selected in a Writer document

        Covers the caret offset inside its paragraph, that paragraph's text and
        the selected text. paragraph_index and document_offset additionally
        require walking the body paragraphs, so they cost one UNO call per
        paragraph up to the caret and are None when the caret sits outside the
        body text, e.g. in a table cell or a frame.
        """
        try:
            doc, error = self._writer_document(doc, "Cursor info")
            if error:
                return error

            controller = doc.getCurrentController()
            view_cursor = controller.getViewCursor() if controller else None
            if not view_cursor:
                return {
                    "success": False,
                    "error": "Document has no view cursor (is LibreOffice running headless?)"
                }

            caret = view_cursor.getStart()
            address, paragraph_cursor, chars_before = self._locate_range(doc, caret)
            index = address["paragraph"]
            offset_in_paragraph = address["offset"]

            info = {
                "success": True,
                "cursor": {
                    "paragraph_index": index,
                    "offset_in_paragraph": offset_in_paragraph,
                    "document_offset": None if chars_before is None
                                       else chars_before + offset_in_paragraph,
                    "page": self._get_page(view_cursor)
                },
                "paragraph": _text_payload(paragraph_cursor.getString()),
                "selection": self._get_selection_info(controller)
            }
            logger.info("Retrieved cursor info")
            return info

        except Exception as e:
            logger.error(f"Failed to get cursor info: {e}")
            return {"success": False, "error": str(e)}

    def read_paragraphs(self, start: int = 0,
                        count: int = DEFAULT_PARAGRAPH_COUNT,
                        doc: Any = None) -> Dict[str, Any]:
        """
        Read a window of body paragraphs with their indices and styles

        count is capped at MAX_PARAGRAPH_COUNT. total_paragraphs always
        reflects the whole document, so the caller can page through it.
        """
        try:
            doc, error = self._writer_document(doc, "Reading paragraphs")
            if error:
                return error

            if not isinstance(start, int) or isinstance(start, bool) or start < 0:
                return {"success": False,
                        "error": f"start must be a non-negative integer, got {start!r}"}

            window = max(1, min(int(count), MAX_PARAGRAPH_COUNT))
            paragraphs = []
            total = 0

            enumeration = doc.getText().createEnumeration()
            while enumeration.hasMoreElements():
                element = enumeration.nextElement()
                if not hasattr(element, "getStart"):
                    continue
                if start <= total < start + window:
                    entry = _text_payload(element.getString())
                    entry["paragraph"] = total
                    entry["style"] = _get_property(element, "ParaStyleName")
                    paragraphs.append(entry)
                total += 1

            return {
                "success": True,
                "paragraphs": paragraphs,
                "start": start,
                "count": len(paragraphs),
                "total_paragraphs": total
            }

        except Exception as e:
            logger.error(f"Failed to read paragraphs: {e}")
            return {"success": False, "error": str(e)}

    def get_outline(self, doc: Any = None) -> Dict[str, Any]:
        """
        List the document's headings with the paragraph index of each

        Gives an assistant a map of a long document without reading it, and
        every entry doubles as an address to read or edit from.
        """
        try:
            doc, error = self._writer_document(doc, "An outline")
            if error:
                return error

            headings = []
            total = 0
            dropped = 0

            enumeration = doc.getText().createEnumeration()
            while enumeration.hasMoreElements():
                element = enumeration.nextElement()
                if not hasattr(element, "getStart"):
                    continue
                level = _heading_level(element)
                if level > 0:
                    if len(headings) < MAX_OUTLINE_ENTRIES:
                        headings.append({
                            "paragraph": total,
                            "level": level,
                            "text": element.getString()[:MAX_TEXT_CHARS]
                        })
                    else:
                        dropped += 1
                total += 1

            if dropped:
                logger.info(f"Outline truncated, {dropped} headings dropped")

            return {
                "success": True,
                "headings": headings,
                "total_paragraphs": total,
                "truncated": dropped > 0
            }

        except Exception as e:
            logger.error(f"Failed to get outline: {e}")
            return {"success": False, "error": str(e)}

    def find_text(self, query: str, regex: bool = False,
                  case_sensitive: bool = False,
                  max_results: int = DEFAULT_SEARCH_RESULTS,
                  doc: Any = None) -> Dict[str, Any]:
        """
        Find text in the active Writer document

        Each hit carries an address that resolves back to the match, so a hit
        can be handed straight to a tool that edits it, plus the containing
        paragraph as context. total_hits is the real number of matches even
        when the list is capped.
        """
        try:
            doc, error = self._writer_document(doc, "Searching")
            if error:
                return error

            if not isinstance(query, str) or not query:
                return {"success": False, "error": "query must be a non-empty string"}

            limit = max(1, min(int(max_results), MAX_SEARCH_RESULTS))

            descriptor = doc.createSearchDescriptor()
            descriptor.SearchString = query
            descriptor.SearchRegularExpression = bool(regex)
            descriptor.SearchCaseSensitive = bool(case_sensitive)

            found = doc.findAll(descriptor)
            total = found.getCount()

            hits = []
            for position in range(min(total, limit)):
                match = found.getByIndex(position)
                address, paragraph_cursor, _ = self._locate_range(doc, match)
                context = _text_payload(paragraph_cursor.getString())
                hits.append({
                    "address": address,
                    "matched": match.getString(),
                    "context": context["text"],
                    "context_truncated": context["truncated"]
                })

            logger.info(f"Found {total} matches for {query!r}, returning {len(hits)}")
            return {
                "success": True,
                "hits": hits,
                "total_hits": total,
                "truncated": total > len(hits)
            }

        except Exception as e:
            logger.error(f"Failed to search: {e}")
            return {"success": False, "error": str(e)}

    def replace_selection(self, text: str, track_changes: Optional[bool] = None,
                          language: Optional[str] = None,
                          flatten: bool = False,
                          doc: Any = None) -> Dict[str, Any]:
        """
        Replace the selected text

        insert_text cannot do this: it calls insertString with bAbsorb=False,
        which inserts at the start of the selection and leaves the original
        behind — asking an assistant to translate and replace produced both
        texts.
        """
        return self._replace(
            {"selection": True}, text, track_changes, doc,
            what="Replacing the selection",
            undo_title="MCP: replace selection",
            empty_error="Nothing is selected, so there is nothing to replace. "
                        "Select the text first, or use a tool that inserts.",
            language=language, flatten=flatten)

    def check_spelling(self, address: Any = None,
                       max_results: int = DEFAULT_SPELLING_RESULTS,
                       doc: Any = None) -> Dict[str, Any]:
        """
        Report misspelled words with an address for each

        Every hit's address resolves back to the word, so it can be handed to
        replace_range. Words are judged against the language of the text
        portion they sit in, not the paragraph's or the document's, so an
        English term inside a Russian sentence is checked as English — the
        distinction that makes the report worth reading at all.

        A language with no dictionary installed is skipped and named rather
        than having all of its words called misspellings.
        """
        doc, error = self._writer_document(doc, "Spell checking")
        if error:
            return error

        try:
            speller = self._spell_checker()
        except Exception as e:
            logger.error(f"No spell checker available: {e}")
            return {"success": False, "error": f"No spell checker available: {e}"}

        if address is not None:
            try:
                index = self._paragraph_index_of(doc, address)
            except AddressError as e:
                return {"success": False, "error": str(e)}
        else:
            index = None

        limit = max(1, min(int(max_results), MAX_SPELLING_RESULTS))
        misspelled = []
        total = 0
        checked_paragraphs = 0
        skipped = []

        for paragraph, position in self._body_paragraphs(doc):
            if index is not None and position != index:
                continue
            checked_paragraphs += 1
            for word, offset, locale in self._words_of(paragraph, speller, skipped):
                if speller.isValid(word, locale, ()):
                    continue
                total += 1
                if len(misspelled) >= limit:
                    continue
                misspelled.append({
                    "word": word,
                    "address": {"paragraph": position, "offset": offset,
                                "length": len(word)},
                    "suggestions": self._suggestions(speller, word, locale),
                    "language": _locale_name(locale)
                })

        logger.info(f"Spell checked {checked_paragraphs} paragraphs, "
                    f"{total} misspellings")
        return {
            "success": True,
            "misspelled": misspelled,
            "total_misspelled": total,
            "truncated": total > len(misspelled),
            "checked_paragraphs": checked_paragraphs,
            "skipped_languages": skipped
        }

    def _spell_checker(self) -> Any:
        """The spell checker, created once per bridge

        The service is created directly rather than through
        LinguServiceManager: the manager's checker resolves in pyuno to the
        XSpellChecker1 overload, which wants a numeric language id and rejects
        every Locale with "Type 17 is not supported".
        """
        speller = getattr(self, "_speller", None)
        if speller is None:
            speller = self.smgr.createInstanceWithContext(
                "com.sun.star.linguistic2.SpellChecker", self.ctx)
            self._speller = speller
        return speller

    def _body_paragraphs(self, doc: Any):
        """Yield (paragraph, index) for the body, skipping tables"""
        position = 0
        enumeration = doc.getText().createEnumeration()
        while enumeration.hasMoreElements():
            element = enumeration.nextElement()
            if not hasattr(element, "getStart"):
                continue
            yield element, position
            position += 1

    def _paragraph_index_of(self, doc: Any, address: Any) -> int:
        """The body paragraph an address points at, for scoping a check"""
        if isinstance(address, dict) and isinstance(address.get("paragraph"), int) \
                and not isinstance(address.get("paragraph"), bool):
            if self._paragraph_at(doc.getText(), address["paragraph"]) is None:
                raise AddressError(f"no body paragraph {address['paragraph']}")
            return address["paragraph"]

        located, _, _ = self._locate_range(doc, self._resolve_address(doc, address))
        if located["paragraph"] is None:
            raise AddressError("that address is outside the body text, so its "
                               "paragraph cannot be spell checked")
        return located["paragraph"]

    def _words_of(self, paragraph: Any, speller: Any, skipped: List[str]):
        """
        Yield (word, offset in paragraph, locale) for a paragraph

        Offsets accumulate across text portions, so the address of a word in
        the third run still points at the right characters.
        """
        offset = 0
        try:
            portions = paragraph.createEnumeration()
        except Exception as e:
            logger.info(f"Could not read the portions of a paragraph: {e}")
            return

        while portions.hasMoreElements():
            portion = portions.nextElement()
            try:
                text = portion.getString()
                locale = portion.CharLocale
            except Exception as e:
                logger.info(f"Skipping an unreadable portion: {e}")
                continue

            name = _locale_name(locale)
            if not name:
                offset += len(text)
                continue

            try:
                known = speller.hasLocale(locale)
            except Exception as e:
                logger.info(f"Could not ask about {name}: {e}")
                known = False

            if not known:
                if name not in skipped:
                    skipped.append(name)
                offset += len(text)
                continue

            for match in WORD.finditer(text):
                yield match.group(), offset + match.start(), locale
            offset += len(text)

    def _suggestions(self, speller: Any, word: str, locale: Any) -> List[str]:
        """What the dictionary offers instead of a word"""
        try:
            alternatives = speller.spell(word, locale, ())
            if alternatives is None:
                return []
            return list(alternatives.getAlternatives())
        except Exception as e:
            logger.info(f"No suggestions for {word!r}: {e}")
            return []

    def set_language(self, address: Any, language: str,
                     doc: Any = None) -> Dict[str, Any]:
        """
        Mark the text at an address as being in a language

        Writer decides which dictionary to spell-check a run against from its
        character locale, so a translation left with the original's locale is
        underlined word by word. This fixes text that is already written;
        replace_range and replace_selection take the same language up front.
        """
        doc, error = self._writer_document(doc, "Setting the language")
        if error:
            return error

        if _is_readonly(doc):
            return {"success": False,
                    "error": "The document is read-only, so it cannot be edited"}

        try:
            locale = _locale(language)
            target = self._resolve_address(doc, address)
        except AddressError as e:
            return {"success": False, "error": str(e)}

        undo = _get_property(doc, "UndoManager", None)
        if undo:
            undo.enterUndoContext("MCP: set language")
        try:
            target.CharLocale = locale
        except Exception as e:
            logger.error(f"Failed to set the language: {e}")
            return {"success": False, "error": str(e)}
        finally:
            if undo:
                undo.leaveUndoContext()

        logger.info(f"Marked text as {language}")
        return {
            "success": True,
            "language": _locale_name(locale),
            "characters": len(target.getString())
        }

    def replace_range(self, address: Any, text: str,
                      track_changes: Optional[bool] = None,
                      language: Optional[str] = None,
                      flatten: bool = False,
                      doc: Any = None) -> Dict[str, Any]:
        """
        Replace the text at an address

        This is what makes the addresses from get_outline and find_text
        actionable without a human selecting anything, which is what an
        assistant needs to rewrite a heading or every match of a search.
        An empty paragraph is a legitimate target, so emptiness is no error
        here, unlike with a selection.
        """
        return self._replace(address, text, track_changes, doc,
                             what="Replacing text",
                             undo_title="MCP: replace text",
                             empty_error=None, language=language,
                             flatten=flatten)

    def _replace(self, address: Any, text: str, track_changes: Optional[bool],
                 doc: Any, what: str, undo_title: str,
                 empty_error: Optional[str],
                 language: Optional[str] = None,
                 flatten: bool = False) -> Dict[str, Any]:
        """
        Rewrite the range an address points at, as a single undo step

        track_changes has three states, because two were not enough. None, the
        default, leaves the document's own recording setting alone: the edit is
        recorded if the document records, and the result says which happened.
        True records this edit even in a document that does not. False refuses
        to record it even in a document that does — the opt-out has to actually
        opt out, since a recorded replacement keeps the original struck through
        and reads as the replacement having failed. Either override is undone
        afterwards, so the document keeps the setting its owner chose.
        """
        doc, error = self._writer_document(doc, what)
        if error:
            return error

        if not isinstance(text, str):
            return {"success": False,
                    "error": f"text must be a string, got {type(text).__name__}"}

        if _is_readonly(doc):
            return {"success": False,
                    "error": "The document is read-only, so it cannot be edited"}

        try:
            locale = _locale(language) if language else None
            target = self._resolve_address(doc, address)
        except AddressError as e:
            return {"success": False, "error": str(e)}

        replaced = target.getString()
        if empty_error and not replaced:
            return {"success": False, "error": empty_error}

        loss = None
        try:
            located, paragraph_cursor, _ = self._locate_range(doc, target)
            paragraph_index = located["paragraph"]
            if paragraph_index is not None:
                loss = self._flattening_loss(doc, located, paragraph_cursor)
        except Exception as e:
            # Naming the paragraph is a nicety; failing to do so must not stop
            # the edit, and must not escape as an exception either.
            logger.info(f"Could not locate the range: {e}")
            paragraph_index = None

        if loss and not flatten:
            details = [f"{loss['runs']} formatted runs"]
            if loss["links"]:
                details.append(f"{loss['links']} hyperlink"
                               f"{'s' if loss['links'] > 1 else ''}")
            if loss["comments"]:
                details.append(f"{loss['comments']} comment"
                               f"{'s' if loss['comments'] > 1 else ''}")
            if loss.get("inline_images"):
                details.append(f"{loss['inline_images']} inline picture"
                               f"{'s' if loss['inline_images'] > 1 else ''}")
            if loss["styles"]:
                details.append(f"{loss['styles']} with character styles")
            destroyed = (" An inline picture is destroyed outright, not just "
                         "flattened." if loss.get("inline_images") else "")
            return {
                "success": False,
                "error": f"This range holds {', '.join(details)}. Replacing it "
                         f"with one string would flatten them: inline code, "
                         f"italics and hyperlinks would be lost.{destroyed} "
                         f"Read it with read_runs, translate each run's text, "
                         f"and write it back with replace_runs — or pass "
                         f"flatten=true to accept the loss."
            }

        recording = bool(_get_property(doc, "RecordChanges", False))
        wanted = recording if track_changes is None else bool(track_changes)
        override = wanted != recording
        undo = _get_property(doc, "UndoManager", None)

        if undo:
            undo.enterUndoContext(undo_title)
        try:
            if override:
                doc.RecordChanges = wanted
            target.setString(text)
            if locale is not None:
                # Without this the new text keeps the locale of what it
                # replaced, and a translation is underlined word by word.
                target.CharLocale = locale
        except Exception as e:
            logger.error(f"Failed to replace text: {e}")
            return {"success": False, "error": str(e)}
        finally:
            # Put the document's own setting back: this edit was recorded or
            # not as asked, but the owner's preference is not changed for them.
            if override:
                try:
                    doc.RecordChanges = recording
                except Exception as e:
                    logger.error(f"Could not restore RecordChanges: {e}")
            if undo:
                undo.leaveUndoContext()

        logger.info(f"Replaced {len(replaced)} characters with {len(text)}")
        return {
            "success": True,
            "replaced_length": len(replaced),
            "inserted_length": len(text),
            "paragraph": paragraph_index,
            "total_paragraphs": self._count_body_paragraphs(doc),
            "tracked": wanted,
            "language": _locale_name(locale) if locale is not None else None,
            "runs_flattened": loss["runs"] if loss else None,
            "links_dropped": loss["links"] if loss else None,
            "comments_dropped": loss["comments"] if loss else None,
            "images_dropped": loss.get("inline_images") if loss else None
        }

    def _guarded_edit(self, doc: Any, undo_title: str,
                      track_changes: Optional[bool], edit) -> Dict[str, Any]:
        """
        Run edit() as one undo step, under the guards every mutation shares

        Refuses a read-only document, honours the three states of
        track_changes and puts the document's own setting back, and reports
        what edit() returns alongside whether the change was recorded.
        """
        if _is_readonly(doc):
            return {"success": False,
                    "error": "The document is read-only, so it cannot be edited"}

        recording = bool(_get_property(doc, "RecordChanges", False))
        wanted = recording if track_changes is None else bool(track_changes)
        override = wanted != recording
        undo = _get_property(doc, "UndoManager", None)

        if undo:
            undo.enterUndoContext(undo_title)
        try:
            if override:
                doc.RecordChanges = wanted
            outcome = edit()
        except AddressError as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            logger.error(f"{undo_title} failed: {e}")
            return {"success": False, "error": str(e)}
        finally:
            if override:
                try:
                    doc.RecordChanges = recording
                except Exception as e:
                    logger.error(f"Could not restore RecordChanges: {e}")
            if undo:
                undo.leaveUndoContext()

        result = {"success": True, "tracked": wanted}
        result.update(outcome or {})
        return result

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
            return {"success": False, "error": str(e)}

        if not asked:
            return {"success": False,
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

    def read_runs(self, address: Any, doc: Any = None) -> Dict[str, Any]:
        """
        The formatted runs the text at an address is made of

        Needed because replacing a mixed-formatting range flattens it: one
        setString over four runs leaves one run, so a monospace term loses its
        font and a coloured phrase loses its colour. With the runs read out,
        text can be translated piece by piece and written back through
        replace_runs with each piece's look restored.

        Every run carries an address that resolves to exactly that run.
        """
        doc, error = self._writer_document(doc, "Reading runs")
        if error:
            return error

        try:
            target = self._resolve_address(doc, address)
            located, paragraph_cursor, _ = self._locate_range(doc, target)
        except AddressError as e:
            return {"success": False, "error": str(e)}

        index = located["paragraph"]
        if index is None:
            return {"success": False,
                    "error": "That address is outside the body text, so its "
                             "runs cannot be read"}

        try:
            runs = self._runs_in(doc, located, paragraph_cursor)
        except Exception as e:
            logger.error(f"Could not read the runs: {e}")
            return {"success": False, "error": str(e)}

        return {"success": True, "runs": runs, "count": len(runs),
                "paragraph": index}

    def _runs_in(self, doc: Any, located: Dict[str, Any],
                 paragraph_cursor: Any) -> List[Dict[str, Any]]:
        """The runs a located range covers, clipped to it"""
        index = located["paragraph"]
        span_start = located["offset"]
        span_end = span_start + max(located["length"], 0)
        if span_end == span_start:
            span_end = span_start + len(paragraph_cursor.getString())

        paragraph = self._paragraph_at(doc.getText(), index)

        # Comments are empty marker portions — Annotation ... AnnotationEnd
        # around a commented range, or a lone Annotation for a point anchor —
        # and they occupy no characters. Collect the portions first, then work
        # out which comment covers which stretch; attaching them while walking
        # counted a range comment twice.
        collected = []
        offset = 0
        portions = paragraph.createEnumeration()
        while portions.hasMoreElements():
            portion = portions.nextElement()
            kind = _get_property(portion, "TextPortionType", "Text")
            if kind in ("Annotation", "AnnotationEnd"):
                collected.append((kind, offset, portion, ""))
                continue
            try:
                body = portion.getString()
            except Exception:
                continue
            collected.append(("Text", offset, portion, body))
            offset += len(body)

        spans = []
        pending = []
        for kind, at, portion, _body in collected:
            if kind == "Annotation":
                note = _get_property(portion, "TextField", None)
                if note is not None:
                    pending.append((note, at))
            elif kind == "AnnotationEnd" and pending:
                note, opened = pending.pop()
                spans.append((_describe_comment(note), opened, at))
        for note, at in pending:            # never closed: a point anchor
            spans.append((_describe_comment(note), at, at))

        pictures = self._images_in(doc, index, span_start, span_end)

        runs = []
        for kind, start_at, portion, body in collected:
            if kind != "Text" or not body:
                continue
            end_at = start_at + len(body)
            if end_at <= span_start or start_at >= span_end:
                continue

            clipped_start = max(start_at, span_start)
            clipped_end = min(end_at, span_end)
            described_run = self._describe_run(
                portion, body[clipped_start - start_at:clipped_end - start_at],
                index, clipped_start)
            described_run["comments"] = [
                note for note, opened, closed in spans
                if (opened < end_at and closed > start_at)
                or (opened == closed and start_at <= opened < end_at)]
            # A picture is an empty portion of type "Frame" at its anchor
            # offset, so it was skipped as an empty run and nothing reported
            # it. It travels on the run it is anchored inside.
            described_run["images"] = [
                image for image in pictures
                if clipped_start <= (image["address"] or {}).get("offset", -1)
                < clipped_end or (clipped_end == span_end
                                  and (image["address"] or {}).get("offset")
                                  == clipped_end)]
            runs.append(described_run)
        return runs

    def _flattening_loss(self, doc: Any, located: Dict[str, Any],
                         paragraph_cursor: Any) -> Optional[Dict[str, Any]]:
        """
        What a flat replacement of this range would destroy, or None

        setString over a range of several runs collapses them into one, and a
        hyperlink is lost even when it is the only run — both measured. A
        character style on a single run survives, so it is not a loss.
        """
        try:
            runs = self._runs_in(doc, located, paragraph_cursor)
        except Exception as e:
            # Unable to tell: better to let the edit through than to block it
            # on a failure to introspect.
            logger.info(f"Could not count the runs before replacing: {e}")
            return None

        links = [run for run in runs if run.get("link")]
        comments = _distinct_comments(runs)
        pictures = _distinct_images(runs)
        inline = [image for image in pictures if image.get("inline")]
        if len(runs) <= 1 and not links and not comments and not inline:
            return None
        return {"runs": len(runs), "links": len(links),
                "comments": len(comments),
                "images": len(pictures), "inline_images": len(inline),
                "styles": len([r for r in runs if r.get("character_style")])}

    def _describe_run(self, portion: Any, body: str, paragraph: int,
                      offset: int) -> Dict[str, Any]:
        """One run as a caller sees it: its text, where it is, how it looks"""
        colour = _get_property(portion, "CharColor", -1)
        background = _get_property(portion, "CharBackColor", -1)
        weight = _get_property(portion, "CharWeight", 100.0) or 100.0
        posture = _get_property(portion, "CharPosture", None)
        return {
            "text": _text_payload(body)["text"],
            "length": len(body),
            "address": {"paragraph": paragraph, "offset": offset,
                        "length": len(body)},
            "bold": weight > 120.0,
            "italic": _is_italic(posture),
            "underline": bool(_get_property(portion, "CharUnderline", 0)),
            "font_name": _get_property(portion, "CharFontName"),
            "font_size": _get_property(portion, "CharHeight"),
            # -1 is "automatic", which is not a colour anyone chose
            "color": None if colour in (-1, None) else _colour_name(colour & 0xFFFFFF),
            "background_color": (None if background in (-1, None)
                                 else _colour_name(background & 0xFFFFFF)),
            "language": _locale_name(_get_property(portion, "CharLocale", None)),
            # A hyperlink is a property of the run, not of the text, and it is
            # lost outright by a plain replacement — which is what makes
            # reading it here the difference between keeping and destroying it.
            "link": _get_property(portion, "HyperLinkURL", "") or None,
            "link_target": _get_property(portion, "HyperLinkTarget", "") or None,
            "character_style": _get_property(portion, "CharStyleName", "") or None
        }

    def _plan_run_rewrite(self, existing_runs: List[Dict[str, Any]],
                          prepared: List[tuple]) -> Dict[str, Any]:
        """
        Work out which runs to leave alone so their comments survive

        Rewriting text under a comment destroys the comment: the annotation
        must be created again, and a new one cannot carry back its id, its
        date, or the language its text was typed in. So a run whose text has
        not changed and which carries a comment is left alone.

        Two boundary facts, both measured on a live LibreOffice:

        * A stretch that begins exactly where a comment's anchor ends
          swallows the AnnotationEnd marker, and the comment goes with it.
          Beginning one character later keeps it, which is possible when that
          first character does not change; when it does, the comment cannot
          be kept and is written again instead.
        * A stretch that *ends* where a comment's anchor begins is harmless.
        """
        notes = []
        for note in _distinct_comments(existing_runs):
            covered = {position for position, run in enumerate(existing_runs)
                       if any(other is note for other in run.get("comments") or [])}
            if covered:
                notes.append((note, covered))

        # An inline picture is destroyed by a replacement of the text it sits
        # in, and unlike a comment it cannot be handed back through a tool
        # call: the only way to keep it is to leave that run alone.
        pictures = []
        for image in _distinct_images(existing_runs):
            if not image.get("inline"):
                continue
            covered = {position for position, run in enumerate(existing_runs)
                       if any(other.get("name") == image.get("name")
                              for other in run.get("images") or [])}
            if covered:
                pictures.append((image, covered))

        if not existing_runs or len(existing_runs) != len(prepared):
            return {"keep": set(), "kept": [],
                    "at_risk": [note for note, _ in notes],
                    "images_kept": [],
                    "images_at_risk": [image for image, _ in pictures],
                    "segments": [{"first": 0, "last": len(prepared) - 1,
                                  "skip_first": False}]}

        unchanged = {position for position, (old, new)
                     in enumerate(zip(existing_runs, prepared))
                     if old["text"] == new[0]}
        attachments = notes + pictures
        candidates = [(thing, covered) for thing, covered in attachments
                      if covered <= unchanged]

        while True:
            keep = set()
            for _thing, covered in candidates:
                keep |= covered
            # A comment only partly inside the kept runs would have its
            # markers rewritten anyway, so none of its runs may be kept.
            for _thing, covered in attachments:
                if covered - keep and covered & keep:
                    keep -= covered

            segments = []
            for position in range(len(prepared)):
                if position in keep:
                    continue
                if segments and segments[-1]["last"] == position - 1:
                    segments[-1]["last"] = position
                else:
                    segments.append({"first": position, "last": position,
                                     "skip_first": False})

            ends = {}
            for thing, covered in candidates:
                if covered <= keep:
                    last = max(covered)
                    ends[existing_runs[last]["address"]["offset"]
                         + existing_runs[last]["length"]] = thing

            giving_up = None
            for segment in segments:
                offset = existing_runs[segment["first"]]["address"]["offset"]
                if offset not in ends:
                    continue
                old_first = existing_runs[segment["first"]]["text"][:1]
                new_first = prepared[segment["first"]][0][:1]
                if old_first and old_first == new_first:
                    segment["skip_first"] = True
                else:
                    giving_up = ends[offset]
                    break

            if giving_up is None:
                return {"keep": keep,
                        "kept": [note for note, covered in notes
                                 if covered and covered <= keep],
                        "at_risk": [note for note, covered in notes
                                    if covered - keep],
                        "images_kept": [image for image, covered in pictures
                                        if covered and covered <= keep],
                        "images_at_risk": [image for image, covered in pictures
                                           if covered - keep],
                        "segments": segments}
            candidates = [(thing, covered) for thing, covered in candidates
                          if thing is not giving_up]

    def replace_runs(self, address: Any, runs: Any,
                     track_changes: Optional[bool] = None,
                     doc: Any = None) -> Dict[str, Any]:
        """
        Replace a range with a sequence of runs, each formatted explicitly

        This is how text keeps its look through a translation. Formatting is
        applied per run afterwards rather than relied upon to be inherited:
        setString takes its properties from the surrounding text in ways that
        are not worth predicting.
        """
        doc, error = self._writer_document(doc, "Replacing runs")
        if error:
            return error

        if not isinstance(runs, (list, tuple)) or not runs:
            return {"success": False,
                    "error": "runs must be a list with at least one run"}

        prepared = []
        for position, run in enumerate(runs):
            if not isinstance(run, dict) or not isinstance(run.get("text"), str):
                return {"success": False,
                        "error": f"run {position} needs a text string"}
            try:
                formatting = self._formatting_of(run)
                language = _locale(run["language"]) if run.get("language") else None
            except AddressError as e:
                return {"success": False, "error": f"run {position}: {e}"}
            comments = run.get("comments") or []
            if not isinstance(comments, (list, tuple)):
                return {"success": False,
                        "error": f"run {position}: comments must be a list"}
            prepared.append((run["text"], formatting, language, list(comments)))

        try:
            target = self._resolve_address(doc, address)
            located, located_cursor, _ = self._locate_range(doc, target)
        except AddressError as e:
            return {"success": False, "error": str(e)}

        if located["paragraph"] is None:
            return {"success": False,
                    "error": "That address is outside the body text, so runs "
                             "cannot be written into it"}

        paragraph = located["paragraph"]
        start = located["offset"]

        # Rewriting text under a comment destroys the comment: the annotation
        # has to be created again, and a new annotation cannot carry back
        # everything the old one had — its date, its id, and the language its
        # text was typed in, which UNO cannot write at all. So a run that
        # carries a comment and whose text has not changed is left alone, and
        # only what actually changes is rewritten. In a translation the
        # commented terms are usually the ones that stay.
        try:
            existing_runs = self._runs_in(doc, located, located_cursor)
        except Exception as e:
            logger.info(f"Could not read the runs before replacing: {e}")
            existing_runs = []

        plan = self._plan_run_rewrite(existing_runs, prepared)
        keep, segments = plan["keep"], plan["segments"]
        kept_notes, at_risk = plan["kept"], plan["at_risk"]

        def placements_for(positions, first_offset):
            """Where each carried comment goes, once per stretch it covers"""
            placed = []
            offset = first_offset
            for position in positions:
                text, _formatting, _language, comments = prepared[position]
                run_end = offset + len(text)
                for comment in comments:
                    key = _comment_key(comment)
                    extended = False
                    for placement in placed:
                        if placement["key"] == key and placement["end"] == offset:
                            placement["end"] = run_end
                            extended = True
                            break
                    if not extended:
                        placed.append({"key": key, "comment": comment,
                                       "start": offset, "end": run_end})
                offset = run_end
            return placed

        if plan["images_at_risk"]:
            names = ", ".join(image.get("name") or "?"
                              for image in plan["images_at_risk"])
            return {
                "success": False,
                "error": f"This range holds {len(plan['images_at_risk'])} "
                         f"inline picture"
                         f"{'s' if len(plan['images_at_risk']) > 1 else ''} "
                         f"({names}) in text you are changing, and replacing "
                         f"that text destroys the picture — a picture cannot "
                         f"be handed back the way a comment can. Pass the run "
                         f"holding it back with its text unchanged and rewrite "
                         f"the runs around it; read_runs says which run that is."
            }

        carried = sum(len(prepared[position][3]) for position in range(len(prepared))
                      if position not in keep)
        if at_risk and not carried:
            return {
                "success": False,
                "error": f"This range carries {len(at_risk)} comment"
                         f"{'s' if len(at_risk) > 1 else ''} on text you are "
                         f"changing, and none of the runs you passed carries "
                         f"one, so they would be lost. Take the comments from "
                         f"read_runs and pass them back on the runs they "
                         f"belong to."
            }

        def edit():
            written_comments = 0
            rewritten = 0
            # Right to left, so the offsets of the earlier segments still hold
            # after a segment has been replaced with text of another length.
            for segment in reversed(segments):
                first, last = segment["first"], segment["last"]
                positions = list(range(first, last + 1))
                new_text = "".join(prepared[position][0]
                                   for position in positions)
                if keep:
                    span_start = existing_runs[first]["address"]["offset"]
                    span_length = sum(existing_runs[position]["length"]
                                      for position in positions)
                    # Rewriting a stretch that begins exactly where a kept
                    # comment's anchor ends swallows its AnnotationEnd marker
                    # and the comment with it, so such a stretch starts one
                    # character later — which the planner only allows when
                    # that character does not change.
                    written_from = span_start + (1 if segment["skip_first"] else 0)
                    span = self._resolve_address(
                        doc, {"paragraph": paragraph, "offset": written_from,
                              "length": span_length
                                        - (1 if segment["skip_first"] else 0)})
                    span.setString(new_text[1:] if segment["skip_first"]
                                   else new_text)
                else:
                    span_start = start
                    target.setString(new_text)
                rewritten += len(positions)

                offset = span_start
                for position in positions:
                    text, formatting, language, _comments = prepared[position]
                    if text and (formatting or language):
                        run_span = self._resolve_address(
                            doc, {"paragraph": paragraph, "offset": offset,
                                  "length": len(text)})
                        if formatting:
                            self._apply_character_formatting(run_span, formatting)
                        if language is not None:
                            run_span.CharLocale = language
                    offset += len(text)

                for placement in placements_for(positions, span_start):
                    span = self._resolve_address(
                        doc, {"paragraph": paragraph,
                              "offset": placement["start"],
                              "length": placement["end"] - placement["start"]})
                    self._anchor_comment(doc, span, placement["comment"])
                    written_comments += 1

            return {"runs": len(prepared), "runs_rewritten": rewritten,
                    "runs_kept": len(keep), "paragraph": paragraph,
                    "characters": sum(len(text) for text, _, _, _ in prepared),
                    "comments_kept": len(kept_notes),
                    "comments_written": written_comments,
                    "images_kept": len(plan["images_kept"])}

        return self._guarded_edit(doc, "MCP: replace runs", track_changes, edit)

    def _anchor_comment(self, doc: Any, span: Any, comment: Dict[str, Any]):
        """
        Put a comment on a range, keeping its author and text

        Gives it a Name if it has none. Writer names the comments made in its
        own interface, but one created through the API comes back with an
        empty Name — measured — and a comment with no name cannot be picked
        out for editing or deleting later.
        """
        note = doc.createInstance(ANNOTATION_SERVICE)
        note.Author = str(comment.get("author", "") or "")
        note.Content = str(comment.get("content", "") or "")
        _stamp_comment(note)
        if comment.get("resolved"):
            try:
                note.Resolved = True
            except Exception as e:
                logger.info(f"Could not mark a comment resolved: {e}")
        span.getText().insertTextContent(span, note, True)
        if not (_get_property(note, "Name", "") or ""):
            try:
                note.Name = f"__Annotation__mcp_{uuid.uuid4().hex[:16]}"
            except Exception as e:
                logger.info(f"Could not name a comment: {e}")
        return note

    def _section_bounds(self, doc: Any, index: Any) -> tuple:
        """
        (first, last) body paragraph of the section a heading opens

        A section runs from its heading to the paragraph before the next
        heading of the same or a higher level, which is what a reader means
        by "this section" — the sub-sections under it included.
        """
        if not isinstance(index, int) or isinstance(index, bool):
            raise AddressError(f"heading must be a paragraph index, "
                               f"got {index!r}")

        levels = []
        enumeration = doc.getText().createEnumeration()
        while enumeration.hasMoreElements():
            element = enumeration.nextElement()
            if not hasattr(element, "getStart"):
                continue
            levels.append(_heading_level(element))

        if index < 0 or index >= len(levels):
            raise AddressError(f"no body paragraph {index}, so no heading there")
        level = levels[index]
        if level <= 0:
            raise AddressError(f"paragraph {index} is not a heading, so it "
                               f"opens no section — take a heading's paragraph "
                               f"index from get_outline")

        last = len(levels) - 1
        for position in range(index + 1, len(levels)):
            if 0 < levels[position] <= level:
                last = position - 1
                break
        return index, last

    def _comment_scope(self, doc: Any, address: Any) -> tuple:
        """
        (predicate on a comment's address, description of the scope)

        Comments can be asked for by document, by section, by paragraph, by an
        exact range or by what is selected. A comment matches a range when its
        anchor overlaps it; a point anchor matches when it sits inside.
        """
        if address is None:
            return (lambda located: True), {"document": True}
        if not isinstance(address, dict):
            raise AddressError(f"address must be an object, got {address!r}")

        if "heading" in address:
            first, last = self._section_bounds(doc, address["heading"])
            def in_section(located):
                return located is not None and located.get("paragraph") \
                    is not None and first <= located["paragraph"] <= last
            return in_section, {"heading": address["heading"],
                                "paragraphs": [first, last]}

        asks_for_a_range = (address.get("selection")
                            or address.get("offset") is not None
                            or address.get("length") is not None)
        if asks_for_a_range:
            located, _, _ = self._locate_range(
                doc, self._resolve_address(doc, address))
            if located.get("paragraph") is None:
                raise AddressError("that address is outside the body text")
            paragraph = located["paragraph"]
            start = located["offset"]
            end = start + located["length"]
            if end == start and address.get("selection"):
                # Nothing selected, only a caret: the paragraph is what the
                # caller can have meant.
                return (lambda l: l is not None
                        and l.get("paragraph") == paragraph), \
                    {"paragraph": paragraph}

            def overlaps(l):
                if l is None or l.get("paragraph") != paragraph:
                    return False
                if l["length"] == 0:
                    return start <= l["offset"] <= end
                return l["offset"] < end and l["offset"] + l["length"] > start
            return overlaps, {"paragraph": paragraph, "offset": start,
                              "length": end - start}

        index = self._paragraph_index_of(doc, address)
        return (lambda l: l is not None and l.get("paragraph") == index), \
            {"paragraph": index}

    def _find_comment(self, doc: Any, comment_id: Any) -> Any:
        """The annotation whose Name is comment_id, or None"""
        if not isinstance(comment_id, str) or not comment_id:
            raise AddressError("comment_id must be the id of a comment, as "
                               "list_comments reports it")
        fields = doc.getTextFields().createEnumeration()
        while fields.hasMoreElements():
            field = fields.nextElement()
            if not _supports(field, ANNOTATION_SERVICE):
                continue
            if (_get_property(field, "Name", "") or "") == comment_id:
                return field
        return None

    def update_comment(self, comment_id: str, text: Optional[str] = None,
                       author: Optional[str] = None,
                       resolved: Optional[bool] = None,
                       language: Optional[str] = None,
                       doc: Any = None) -> Dict[str, Any]:
        """
        Change a comment's text, author, language or resolved state

        The text the comment is anchored to is untouched: this edits the note
        in the margin, not the document.

        Text, author and resolved are changed in place, so the comment keeps
        its id and its date. A `language` cannot be: Writer marks a note when
        the note is created, so the comment is made again on the same anchor —
        with a new id and today's date — and the language of the document's
        comments is set along with it, since one comment cannot have its own.
        The result says both.
        """
        doc, error = self._writer_document(doc, "Editing a comment")
        if error:
            return error

        if text is None and author is None and resolved is None \
                and language is None:
            return {"success": False,
                    "error": "Nothing to change: pass text, author, language "
                             "or resolved"}
        if language is not None:
            try:
                _locale(language)
                self._comment_style(doc)
            except AddressError as e:
                return {"success": False, "error": str(e)}
        if text is not None and (not isinstance(text, str) or not text):
            return {"success": False,
                    "error": "text must be a non-empty string; to remove a "
                             "comment use delete_comment"}

        try:
            note = self._find_comment(doc, comment_id)
        except AddressError as e:
            return {"success": False, "error": str(e)}
        if note is None:
            return {"success": False,
                    "error": f"No comment with id {comment_id} in this "
                             f"document. Take an id from list_comments."}

        was = _describe_comment(note)
        anchor_address = None
        if language is not None:
            try:
                located, _, _ = self._locate_range(doc, note.getAnchor())
                anchor_address = located
            except Exception as e:
                logger.info(f"Could not locate a comment's anchor: {e}")
            if anchor_address is None or anchor_address.get("paragraph") is None:
                return {"success": False,
                        "error": "This comment's anchor is outside the body "
                                 "text, so it cannot be made again in another "
                                 "language"}

        def edit():
            changed = []
            remade = None

            if language is not None:
                # Only a new note can carry a language, so make this one
                # again on the same anchor.
                wanted = {"author": author if author is not None
                          else was["author"],
                          "content": text if text is not None else was["content"],
                          "resolved": was["resolved"] if resolved is None
                          else bool(resolved)}
                previous_language = self._set_comment_style_language(doc,
                                                                     language)
                note.getAnchor().getText().removeTextContent(note)
                span = self._resolve_address(doc, anchor_address)
                remade = self._anchor_comment(doc, span, wanted)
                changed.append("language")
                if text is not None:
                    changed.append("text")
                if author is not None:
                    changed.append("author")
                if resolved is not None:
                    changed.append("resolved")
                described = _describe_comment(remade)
                return {"id": described["id"], "previous_id": was["id"],
                        "recreated": True, "changed": changed,
                        "author": described["author"],
                        "content": described["content"],
                        "resolved": described["resolved"],
                        "language": described["language"],
                        "comment_language_set": {
                            "language": language, "was": previous_language,
                            "scope": "the document's comments: a language "
                                     "cannot be given to one comment alone"}}

            if text is not None:
                _write_comment_text(note, text)
                changed.append("text")
            if author is not None:
                note.Author = str(author)
                changed.append("author")
            if resolved is not None:
                note.Resolved = bool(resolved)
                changed.append("resolved")
            described = _describe_comment(note)
            return {"id": described["id"], "changed": changed,
                    "recreated": False,
                    "author": described["author"],
                    "content": described["content"],
                    "resolved": described["resolved"],
                    "language": described["language"]}

        return self._guarded_edit(doc, "MCP: edit comment", None, edit)

    def delete_comment(self, comment_id: str, doc: Any = None) -> Dict[str, Any]:
        """
        Remove a comment, leaving the text it was anchored to

        Returns what was deleted, so an assistant can say what it removed —
        and so the text can be commented again if that was a mistake.
        """
        doc, error = self._writer_document(doc, "Deleting a comment")
        if error:
            return error

        try:
            note = self._find_comment(doc, comment_id)
        except AddressError as e:
            return {"success": False, "error": str(e)}
        if note is None:
            return {"success": False,
                    "error": f"No comment with id {comment_id} in this "
                             f"document. Take an id from list_comments."}

        described = _describe_comment(note)
        anchor_text = None
        try:
            anchor_text = _text_payload(note.getAnchor().getString())["text"]
        except Exception as e:
            logger.info(f"Could not read a comment's anchor: {e}")

        def edit():
            anchor = note.getAnchor()
            anchor.getText().removeTextContent(note)
            return {"id": described["id"], "author": described["author"],
                    "content": described["content"],
                    "anchor_text": anchor_text}

        return self._guarded_edit(doc, "MCP: delete comment", None, edit)

    def _comment_style(self, doc: Any) -> Any:
        """The "Comment" paragraph style, which notes take their language from"""
        family = doc.StyleFamilies.getByName("ParagraphStyles")
        if not family.hasByName(COMMENT_STYLE):
            raise AddressError(f'this document has no "{COMMENT_STYLE}" '
                               f'paragraph style, so the language of its '
                               f'comments cannot be set')
        return family.getByName(COMMENT_STYLE)

    def _set_comment_style_language(self, doc: Any, language: str) -> Any:
        """
        Set the language the document's comments are written in

        It has to stay set: a note whose style is put back afterwards reports
        the language it was put back to, so there is no way to give one
        comment a language of its own.
        """
        style = self._comment_style(doc)
        was = _locale_name(_get_property(style, "CharLocale", None))
        style.CharLocale = _locale(language)
        return was

    def set_comment_language(self, language: str,
                             doc: Any = None) -> Dict[str, Any]:
        """
        Set the language the document's comments are written in

        Writer spell checks a note in the margin against the language of the
        note's own text, which it takes from the "Comment" paragraph style
        when the note is created. Setting the style therefore marks the
        comments added from now on and leaves the ones already there as they
        are — update_comment changes one of those, by making it again.
        """
        doc, error = self._writer_document(doc, "Setting the comment language")
        if error:
            return error

        try:
            locale = _locale(language)
            style = self._comment_style(doc)
        except AddressError as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            logger.error(f"Could not reach the comment style: {e}")
            return {"success": False, "error": str(e)}

        was = _locale_name(_get_property(style, "CharLocale", None))

        def edit():
            style.CharLocale = locale
            return {"language": _locale_name(_get_property(style, "CharLocale",
                                                           None)),
                    "was": was,
                    "comments_already_there": len(self._annotations(doc)),
                    "scope": "the comments added from now on; the ones "
                             "already in the document keep the language they "
                             "were written in, and update_comment can change "
                             "one of those"}

        return self._guarded_edit(doc, "MCP: set comment language", None, edit)

    def _graphics(self, doc: Any) -> List[Any]:
        """Every picture in the document, in the order it names them"""
        try:
            graphics = doc.getGraphicObjects()
            return [graphics.getByName(name)
                    for name in graphics.getElementNames()]
        except Exception as e:
            logger.error(f"Could not enumerate the pictures: {e}")
            return []

    def _describe_image(self, doc: Any, image: Any) -> Dict[str, Any]:
        """
        A picture as a caller sees it: what it is, where it is, what it shows

        `address` is where its anchor sits in the body text, so it can be
        handed to any tool that reads or edits text, and `paragraph_text` is
        the text it is anchored to — the question a caller actually has.
        """
        anchor = _anchor_kind(_get_property(image, "AnchorType", None))
        described = {
            "name": _get_property(image, "Name", "") or "",
            "title": _get_property(image, "Title", "") or None,
            "description": _get_property(image, "Description", "") or None,
            "anchor": anchor,
            "inline": anchor == INLINE_ANCHOR,
            "width_mm": _millimetres(_get_property(image, "Width", None)),
            "height_mm": _millimetres(_get_property(image, "Height", None)),
        }

        graphic = _get_property(image, "Graphic", None)
        described["mime_type"] = None
        described["pixels"] = None
        described["linked"] = None
        described["origin_url"] = None
        if graphic is not None:
            mime = _get_property(graphic, "MimeType", "") or ""
            # image/x-vclgraphic means "whatever LibreOffice holds in memory",
            # which tells a caller nothing about the file it would get.
            described["mime_type"] = mime or None
            pixels = _get_property(graphic, "SizePixel", None)
            if pixels is not None:
                described["pixels"] = {
                    "width": _get_property(pixels, "Width", None),
                    "height": _get_property(pixels, "Height", None)}
            described["linked"] = bool(_get_property(graphic, "Linked", False))
            described["origin_url"] = _get_property(graphic, "OriginURL", "") \
                or None

        described["address"] = None
        described["paragraph_text"] = None
        try:
            located, _, _ = self._locate_range(doc, image.getAnchor())
            described["address"] = located
            if located.get("paragraph") is not None:
                paragraph = self._paragraph_at(doc.getText(),
                                               located["paragraph"])
                if paragraph is not None:
                    described["paragraph_text"] = _text_payload(
                        paragraph.getString())["text"]
        except Exception as e:
            logger.info(f"Could not locate a picture: {e}")
        if described["address"] is None:
            page = _get_property(image, "AnchorPageNo", None)
            described["page"] = page if page else None
        return described

    def _images_in(self, doc: Any, paragraph: int, start: int,
                   end: int) -> List[Dict[str, Any]]:
        """The pictures anchored inside a stretch of a paragraph"""
        found = []
        for image in self._graphics(doc):
            described = self._describe_image(doc, image)
            address = described.get("address") or {}
            if address.get("paragraph") != paragraph:
                continue
            offset = address.get("offset")
            if offset is None or not (start <= offset <= end):
                continue
            found.append(described)
        return found

    def list_images(self, address: Any = None,
                    doc: Any = None) -> Dict[str, Any]:
        """
        The pictures of a document, a section, a paragraph, a range or the
        selection

        Each carries the address of its anchor, the text it is anchored to and
        its own size, so a caller can tell that a selection holds a picture,
        say where it is, and ask for the file with export_image.
        """
        doc, error = self._writer_document(doc, "Listing pictures")
        if error:
            return error

        try:
            covers, scope = self._comment_scope(doc, address)
        except AddressError as e:
            return {"success": False, "error": str(e)}

        images = []
        for image in self._graphics(doc):
            described = self._describe_image(doc, image)
            if not covers(described["address"]):
                continue
            images.append(described)

        images.sort(key=lambda i: (
            (i["address"] or {}).get("paragraph", 10 ** 9),
            (i["address"] or {}).get("offset", 0)))
        return {"success": True, "images": images, "count": len(images),
                "scope": scope}

    def export_image(self, name: str, path: Optional[str] = None,
                     image_format: str = "png", inline: bool = False,
                     doc: Any = None) -> Dict[str, Any]:
        """
        Write a picture to a file, and hand back its bytes if asked

        `name` is the picture's own name, as list_images reports it. The file
        is what LibreOffice re-encodes the picture into, so the size on disk
        is not the size it occupies in the document.
        """
        doc, error = self._writer_document(doc, "Exporting a picture")
        if error:
            return error

        if not isinstance(name, str) or not name:
            return {"success": False,
                    "error": "name must be the name of a picture, as "
                             "list_images reports it"}

        wanted = None
        known = []
        for image in self._graphics(doc):
            image_name = _get_property(image, "Name", "") or ""
            known.append(image_name)
            if image_name == name:
                wanted = image
        if wanted is None:
            return {"success": False,
                    "error": f"No picture named {name} in this document. "
                             f"It holds: {', '.join(known) or 'none'}."}

        graphic = _get_property(wanted, "Graphic", None)
        if graphic is None:
            return {"success": False,
                    "error": f"The picture {name} carries no graphic to write"}

        asked = (image_format or "png").lower()
        own = _get_property(graphic, "MimeType", "") or ""
        if asked == "original":
            mime = own if own in IMAGE_EXTENSIONS else "image/png"
        elif asked in IMAGE_TYPES:
            mime = IMAGE_TYPES[asked]
        else:
            return {"success": False,
                    "error": f'format must be one of '
                             f'{", ".join(sorted(IMAGE_TYPES))} or "original", '
                             f'got {image_format!r}'}

        target = path
        if not target:
            safe = re.sub(r"[^A-Za-z0-9._-]", "_", name) or "image"
            target = os.path.join(tempfile.gettempdir(),
                                  f"{safe}.{IMAGE_EXTENSIONS[mime]}")
        target = os.path.abspath(os.path.expanduser(target))
        directory = os.path.dirname(target)
        if not os.path.isdir(directory):
            return {"success": False,
                    "error": f"There is no directory {directory} to write into"}

        try:
            provider = self.smgr.createInstanceWithContext(
                "com.sun.star.graphic.GraphicProvider", self.ctx)
            url = PropertyValue()
            url.Name, url.Value = "URL", _file_url(target)
            kind = PropertyValue()
            kind.Name, kind.Value = "MimeType", mime
            provider.storeGraphic(graphic, (url, kind))
        except Exception as e:
            logger.error(f"Could not write the picture {name}: {e}")
            return {"success": False, "error": str(e)}

        if not os.path.exists(target):
            return {"success": False,
                    "error": f"LibreOffice reported no error but wrote no "
                             f"file at {target}"}

        described = self._describe_image(doc, wanted)
        result = {"success": True, "name": name, "path": target,
                  "bytes": os.path.getsize(target), "mime_type": mime,
                  "pixels": described["pixels"], "address": described["address"],
                  "title": described["title"],
                  "description": described["description"]}

        if inline:
            size = os.path.getsize(target)
            if size > MAX_INLINE_IMAGE_BYTES:
                result["inline"] = False
                result["inline_refused"] = (
                    f"{size} bytes is more than the {MAX_INLINE_IMAGE_BYTES} "
                    f"a reply carries; read the file at {target} instead")
            else:
                with open(target, "rb") as handle:
                    encoded = base64.b64encode(handle.read()).decode("ascii")
                result["inline"] = True
                result["_image_content"] = {"data": encoded, "mime_type": mime}
        return result

    def _annotations(self, doc: Any) -> List[Any]:
        """Every comment field in the document"""
        found = []
        try:
            fields = doc.getTextFields().createEnumeration()
        except Exception as e:
            logger.error(f"Could not enumerate comments: {e}")
            return found
        while fields.hasMoreElements():
            field = fields.nextElement()
            if _supports(field, ANNOTATION_SERVICE):
                found.append(field)
        return found

    def list_comments(self, address: Any = None,
                      doc: Any = None) -> Dict[str, Any]:
        """
        The comments of a document, a section, a paragraph, a range or the
        selection

        Each carries the address of the text it is anchored to and that text
        itself, so a caller can see what a comment is about without reading
        the whole document, plus the id that names it for editing.
        """
        doc, error = self._writer_document(doc, "Listing comments")
        if error:
            return error

        try:
            covers, scope = self._comment_scope(doc, address)
        except AddressError as e:
            return {"success": False, "error": str(e)}

        comments = []
        try:
            fields = doc.getTextFields().createEnumeration()
        except Exception as e:
            logger.error(f"Could not enumerate comments: {e}")
            return {"success": False, "error": str(e)}

        while fields.hasMoreElements():
            field = fields.nextElement()
            if not _supports(field, ANNOTATION_SERVICE):
                continue
            described = _describe_comment(field)
            try:
                anchor = field.getAnchor()
                located, _, _ = self._locate_range(doc, anchor)
                described["address"] = located
                described["anchor_text"] = _text_payload(anchor.getString())["text"]
            except Exception as e:
                logger.info(f"Could not locate a comment: {e}")
                described["address"] = None
                described["anchor_text"] = None
            if not covers(described["address"]):
                continue
            comments.append(described)

        comments.sort(key=lambda c: (
            (c["address"] or {}).get("paragraph", 10 ** 9),
            (c["address"] or {}).get("offset", 0)))
        return {"success": True, "comments": comments, "count": len(comments),
                "scope": scope}

    def add_comment(self, address: Any, text: str, author: str = "",
                    language: Optional[str] = None,
                    doc: Any = None) -> Dict[str, Any]:
        """
        Anchor a new comment to the text at an address

        `language` marks the note's own text, which is what Writer spell
        checks: a Russian note left at the document's language is underlined
        word by word in the margin. It cannot be given to one comment alone,
        so passing it also sets the language of the document's comments —
        which the result says.
        """
        doc, error = self._writer_document(doc, "Adding a comment")
        if error:
            return error

        if not isinstance(text, str) or not text:
            return {"success": False, "error": "text must be a non-empty string"}

        try:
            target = self._resolve_address(doc, address)
        except AddressError as e:
            return {"success": False, "error": str(e)}

        if language is not None:
            try:
                _locale(language)          # refuse a bad tag before editing
                self._comment_style(doc)
            except AddressError as e:
                return {"success": False, "error": str(e)}

        def edit():
            was = None
            if language is not None:
                was = self._set_comment_style_language(doc, language)
            note = self._anchor_comment(doc, target,
                                        {"author": author, "content": text})
            result = {"id": _get_property(note, "Name", "") or "",
                      "anchor_text": _text_payload(target.getString())["text"],
                      "author": author, "content": text,
                      "language": _comment_language(note)}
            if language is not None:
                result["comment_language_set"] = {
                    "language": language, "was": was,
                    "scope": "the document's comments: a language cannot be "
                             "given to one comment alone"}
            return result

        return self._guarded_edit(doc, "MCP: add comment", None, edit)

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
            return {"success": False, "error": str(e)}

        if not asked:
            return {"success": False,
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
            return {"success": False, "error": "style must be a non-empty string"}

        if not self._has_style(doc, "ParagraphStyles", style):
            return {
                "success": False,
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
                    "error": f"No style family {family!r}. This document has: "
                             f"{', '.join(families.getElementNames())}"
                }
            names = list(families.getByName(family).getElementNames())
        except Exception as e:
            logger.error(f"Could not list styles: {e}")
            return {"success": False, "error": str(e)}

        return {"success": True, "family": family, "styles": names,
                "count": len(names)}

    def _has_style(self, doc: Any, family: str, style: str) -> bool:
        """Whether a document carries a style, False if it cannot be asked"""
        try:
            return bool(doc.StyleFamilies.getByName(family).hasByName(style))
        except Exception as e:
            logger.info(f"Could not check style {style!r}: {e}")
            return False

    def _count_tracked_changes(self, doc: Any) -> Optional[int]:
        """How many recorded changes wait to be accepted, None if unknown"""
        try:
            return doc.getRedlines().getCount()
        except Exception as e:
            logger.debug(f"Could not count tracked changes: {e}")
            return None

    def _count_body_paragraphs(self, doc: Any) -> Optional[int]:
        """How many paragraphs the body holds, None if it cannot be walked"""
        try:
            total = 0
            enumeration = doc.getText().createEnumeration()
            while enumeration.hasMoreElements():
                if hasattr(enumeration.nextElement(), "getStart"):
                    total += 1
            return total
        except Exception as e:
            logger.info(f"Could not count paragraphs: {e}")
            return None

    def _locate_range(self, doc: Any, text_range: Any) -> tuple:
        """
        Locate a range within the document

        Returns (address, paragraph_cursor, chars_before_paragraph), where
        address is {"paragraph": index or None, "offset": int, "length": int},
        paragraph_cursor spans the paragraph holding the range start, and
        chars_before_paragraph is None whenever the index is None.

        The cursors come from the text owning the range, which inside a table
        cell or a frame is not the body text.
        """
        owner = text_range.getText()
        start = text_range.getStart()

        offset_cursor = owner.createTextCursorByRange(start)
        offset_cursor.gotoStartOfParagraph(True)
        offset = len(offset_cursor.getString())

        paragraph_cursor = owner.createTextCursorByRange(start)
        paragraph_cursor.gotoStartOfParagraph(False)
        paragraph_cursor.gotoEndOfParagraph(True)

        index, chars_before = self._locate_paragraph(
            doc.getText(), paragraph_cursor.getStart())

        address = {
            "paragraph": index,
            "offset": offset,
            "length": len(text_range.getString())
        }
        return address, paragraph_cursor, chars_before

    def _paragraph_at(self, text: Any, index: Any) -> Any:
        """
        The index-th body paragraph, or None when there is no such paragraph

        Tables are skipped, so indices match what _locate_paragraph reports.
        """
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise AddressError(
                f"paragraph must be a non-negative integer, got {index!r}")

        position = 0
        enumeration = text.createEnumeration()
        while enumeration.hasMoreElements():
            element = enumeration.nextElement()
            if not hasattr(element, "getStart"):
                continue
            if position == index:
                return element
            position += 1
        return None

    def _position_in(self, paragraph: Any, offset: int) -> Any:
        """
        A collapsed cursor at a character offset inside a paragraph

        Counting the offset with goRight from the paragraph start drifts in a
        paragraph that carries comments: an annotation is anchored
        AS_CHARACTER, so it counts as one position for cursor movement while
        contributing nothing to the string — measured, one position per
        comment before the offset. That is how a comment on the second term
        of a paragraph came to sit over " subscriptio" instead of
        "subscription". Walking the text portions and moving only *inside*
        one keeps positions and characters in step, since portions are split
        at every marker.
        """
        text = paragraph.getText()
        if offset <= 0:
            return text.createTextCursorByRange(paragraph.getStart())

        seen = 0
        try:
            portions = paragraph.createEnumeration()
            while portions.hasMoreElements():
                portion = portions.nextElement()
                if _get_property(portion, "TextPortionType", "Text") != "Text":
                    continue
                body = portion.getString()
                if not body:
                    continue
                if seen + len(body) >= offset:
                    cursor = text.createTextCursorByRange(portion.getStart())
                    if offset - seen:
                        cursor.goRight(offset - seen, False)
                    return cursor
                seen += len(body)
        except Exception as e:
            # Better a possibly drifted cursor than no edit at all, but say so.
            logger.info(f"Could not walk the portions of a paragraph: {e}")
            cursor = text.createTextCursorByRange(paragraph.getStart())
            cursor.goRight(offset, False)
            return cursor

        cursor = text.createTextCursorByRange(paragraph.getStart())
        cursor.gotoEndOfParagraph(False)
        return cursor

    def _resolve_address(self, doc: Any, address: Any) -> Any:
        """
        Turn an address into a text range

        Accepts {"paragraph": i, "offset": k, "length": n} against the body
        text, where offset defaults to 0 and an omitted length means the rest
        of the paragraph, or {"selection": true} for the current selection.
        Raises AddressError for anything it cannot resolve.

        A collapsed selection resolves to an empty range rather than an error:
        inserting at a caret is legitimate, so callers needing actual content
        check for themselves.
        """
        if not isinstance(address, dict):
            raise AddressError(
                f"address must be an object, got {type(address).__name__}")

        if address.get("selection"):
            controller = doc.getCurrentController()
            if not controller:
                raise AddressError("document has no view, so it has no selection")
            try:
                selection = controller.getSelection()
                count = selection.getCount()
            except Exception as e:
                raise AddressError(f"the selection is not a text range: {e}")
            if count < 1:
                raise AddressError("nothing is selected")
            return selection.getByIndex(0)

        if "paragraph" not in address:
            raise AddressError("address needs either 'paragraph' or 'selection'")

        paragraph = self._paragraph_at(doc.getText(), address["paragraph"])
        if paragraph is None:
            raise AddressError(f"no body paragraph {address['paragraph']}")

        paragraph_length = len(paragraph.getString())
        offset = address.get("offset", 0)
        length = address.get("length")

        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0 \
                or offset > paragraph_length:
            raise AddressError(
                f"offset {offset!r} is outside paragraph {address['paragraph']}, "
                f"which holds {paragraph_length} characters")

        cursor = self._position_in(paragraph, offset)

        if length is None:
            cursor.gotoEndOfParagraph(True)
        else:
            if not isinstance(length, int) or isinstance(length, bool) or length < 0 \
                    or offset + length > paragraph_length:
                raise AddressError(
                    f"length {length!r} from offset {offset} runs past the end of "
                    f"paragraph {address['paragraph']}")
            if length:
                cursor.gotoRange(self._position_in(paragraph, offset + length),
                                 True)
        return cursor

    def _locate_paragraph(self, text: Any, paragraph_start: Any) -> tuple:
        """
        Find the caret's paragraph in the body text

        Returns (index, characters before it) or (None, None) when the
        paragraph is not part of the body enumeration. Tables are skipped, so
        their content does not count towards the character total.
        """
        try:
            enumeration = text.createEnumeration()
            index = 0
            chars_before = 0
            while enumeration.hasMoreElements():
                element = enumeration.nextElement()
                if not hasattr(element, "getStart"):
                    continue
                # Only equality matters here, so the sign convention of
                # compareRegionStarts is irrelevant
                if text.compareRegionStarts(element.getStart(), paragraph_start) == 0:
                    return index, chars_before
                chars_before += len(element.getString()) + 1  # + paragraph break
                index += 1
            return None, None
        except Exception as e:
            logger.info(f"No absolute position, caret is outside the body text: {e}")
            return None, None

    def _get_page(self, view_cursor: Any) -> Optional[int]:
        """Page the caret is on, None if the view cannot report one"""
        try:
            return view_cursor.getPage()
        except Exception as e:
            logger.info(f"Page number unavailable: {e}")
            return None

    def _get_selection_info(self, controller: Any) -> Dict[str, Any]:
        """
        Read the selection, joining the parts of a multi-range selection

        A table cell selection is not a collection of text ranges, so it is
        reported as no selection rather than failing the whole call.
        """
        try:
            selection = controller.getSelection()
            range_count = selection.getCount()
            parts = [selection.getByIndex(i).getString() for i in range(range_count)]
        except Exception as e:
            logger.info(f"Selection holds no readable text ranges: {e}")
            return {
                "has_selection": False,
                "text": "",
                "length": 0,
                "range_count": 0,
                "truncated": False
            }

        selected = "\n".join(part for part in parts if part)
        info = _text_payload(selected)
        info["has_selection"] = bool(selected)
        info["range_count"] = range_count
        return info

    def _get_document_type(self, doc: Any) -> str:
        """Determine document type"""
        if _supports(doc, WRITER_SERVICE):
            return "writer"
        elif _supports(doc, CALC_SERVICE):
            return "calc"
        elif _supports(doc, IMPRESS_SERVICE):
            return "impress"
        elif _supports(doc, DRAW_SERVICE):
            return "draw"
        else:
            return "unknown"
    
    def _has_selection(self, doc: Any) -> bool:
        """Check if document has selected content"""
        try:
            if hasattr(doc, 'getCurrentController'):
                controller = doc.getCurrentController()
                if hasattr(controller, 'getSelection'):
                    selection = controller.getSelection()
                    return selection.getCount() > 0
        except:
            pass
        return False
