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
                "has_selection": self._has_selection(doc)
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
            
            # Get current selection
            selection = doc.getCurrentController().getSelection()
            if selection.getCount() == 0:
                return {"success": False, "error": "No text selected"}
            
            # Apply formatting to selection
            text_range = selection.getByIndex(0)
            
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

    def replace_selection(self, text: str, track_changes: bool = False,
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
                      track_changes: bool = False,
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

    def _replace(self, address: Any, text: str, track_changes: bool,
                 doc: Any, what: str, undo_title: str,
                 empty_error: Optional[str],
                 language: Optional[str] = None) -> Dict[str, Any]:
        """
        Rewrite the range an address points at, as a single undo step

        track_changes defaults to false at every caller because a tracked
        replacement keeps the original in the document, struck through until
        someone accepts it, which reads as the replacement having failed.
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

        recording = _get_property(doc, "RecordChanges", None)
        undo = _get_property(doc, "UndoManager", None)

        if undo:
            undo.enterUndoContext(undo_title)
        try:
            if track_changes and recording is False:
                doc.RecordChanges = True
            target.setString(text)
            if locale is not None:
                # Without this the new text keeps the locale of what it
                # replaced, and a translation is underlined word by word.
                target.CharLocale = locale
        except Exception as e:
            logger.error(f"Failed to replace text: {e}")
            return {"success": False, "error": str(e)}
        finally:
            # Restore the document's own setting: the edit stays recorded, but
            # the user's preference is not silently changed underneath them.
            if track_changes and recording is False:
                try:
                    doc.RecordChanges = False
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
            "tracked": bool(track_changes),
            "language": _locale_name(locale) if locale is not None else None
        }

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
