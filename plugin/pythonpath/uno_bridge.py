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
            language=language)

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
                             empty_error=None, language=language)

    def _replace(self, address: Any, text: str, track_changes: Optional[bool],
                 doc: Any, what: str, undo_title: str,
                 empty_error: Optional[str],
                 language: Optional[str] = None) -> Dict[str, Any]:
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

        try:
            located, _, _ = self._locate_range(doc, target)
            paragraph_index = located["paragraph"]
        except Exception as e:
            # Naming the paragraph is a nicety; failing to do so must not stop
            # the edit, and must not escape as an exception either.
            logger.info(f"Could not locate the range: {e}")
            paragraph_index = None

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
            "language": _locale_name(locale) if locale is not None else None
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

        span_start = located["offset"]
        span_end = span_start + max(located["length"], 0)
        if span_end == span_start:
            span_end = span_start + len(paragraph_cursor.getString())

        paragraph = self._paragraph_at(doc.getText(), index)
        runs = []
        offset = 0
        try:
            portions = paragraph.createEnumeration()
        except Exception as e:
            logger.error(f"Could not read the runs: {e}")
            return {"success": False, "error": str(e)}

        while portions.hasMoreElements():
            portion = portions.nextElement()
            try:
                body = portion.getString()
            except Exception:
                continue
            start, end = offset, offset + len(body)
            offset = end
            if not body or end <= span_start or start >= span_end:
                continue

            clipped_start = max(start, span_start)
            clipped_end = min(end, span_end)
            runs.append(self._describe_run(
                portion, body[clipped_start - start:clipped_end - start],
                index, clipped_start))

        return {"success": True, "runs": runs, "count": len(runs),
                "paragraph": index}

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
            "italic": str(posture).endswith("ITALIC"),
            "underline": bool(_get_property(portion, "CharUnderline", 0)),
            "font_name": _get_property(portion, "CharFontName"),
            "font_size": _get_property(portion, "CharHeight"),
            # -1 is "automatic", which is not a colour anyone chose
            "color": None if colour in (-1, None) else _colour_name(colour & 0xFFFFFF),
            "background_color": (None if background in (-1, None)
                                 else _colour_name(background & 0xFFFFFF)),
            "language": _locale_name(_get_property(portion, "CharLocale", None))
        }

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
            prepared.append((run["text"], formatting, language))

        try:
            target = self._resolve_address(doc, address)
            located, _, _ = self._locate_range(doc, target)
        except AddressError as e:
            return {"success": False, "error": str(e)}

        if located["paragraph"] is None:
            return {"success": False,
                    "error": "That address is outside the body text, so runs "
                             "cannot be written into it"}

        paragraph = located["paragraph"]
        start = located["offset"]

        def edit():
            target.setString("".join(text for text, _, _ in prepared))
            offset = start
            for text, formatting, language in prepared:
                if text and (formatting or language):
                    span = self._resolve_address(
                        doc, {"paragraph": paragraph, "offset": offset,
                              "length": len(text)})
                    if formatting:
                        self._apply_character_formatting(span, formatting)
                    if language is not None:
                        span.CharLocale = language
                offset += len(text)
            return {"runs": len(prepared), "paragraph": paragraph,
                    "characters": offset - start}

        return self._guarded_edit(doc, "MCP: replace runs", track_changes, edit)

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

        cursor = paragraph.getText().createTextCursorByRange(paragraph.getStart())
        if offset:
            cursor.goRight(offset, False)

        if length is None:
            cursor.gotoEndOfParagraph(True)
        else:
            if not isinstance(length, int) or isinstance(length, bool) or length < 0 \
                    or offset + length > paragraph_length:
                raise AddressError(
                    f"length {length!r} from offset {offset} runs past the end of "
                    f"paragraph {address['paragraph']}")
            cursor.goRight(length, True)
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
