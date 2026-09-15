"""Documents themselves: finding them, saving, closing, renaming.

getCurrentComponent() follows the focused frame and is not always a document,
so the active one is checked and fallen back on. storeAsURL with no filter
writes ODF whatever the file is called, close(True) discards unsaved changes
without a murmur, and UNO has no rename — each of which shapes a refusal here.
"""

import uno
from com.sun.star.beans import PropertyValue
from typing import Any, Optional, Dict, List
import os
import logging
from uno_values import (AddressError, CALC_SERVICE, DRAW_SERVICE, 
    EXPORT_ONLY_FORMATS, IMPRESS_SERVICE, WRITER_SAVE_FILTERS, 
    WRITER_SERVICE, _document_path, _file_url, _get_document_url, 
    _get_property, _get_property_call, _is_document, _is_readonly, 
    _supports)

logger = logging.getLogger(__name__)


class DocumentsMixin:
    """Part of UNOBridge — see uno_bridge.py for how the parts meet."""

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

    def _count_tracked_changes(self, doc: Any) -> Optional[int]:
        """How many recorded changes wait to be accepted, None if unknown"""
        try:
            return doc.getRedlines().getCount()
        except Exception as e:
            logger.debug(f"Could not count tracked changes: {e}")
            return None

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

    def _save_filter(self, path: str,
                     document_format: Optional[str] = None) -> tuple:
        """(FilterName, the format it stands for) for a path to save to"""
        wanted = (document_format
                  or os.path.splitext(path)[1].lstrip(".")).lower()
        if not wanted:
            raise AddressError(
                f"{os.path.basename(path)} has no extension and no format was "
                f"given, so there is no telling what to write; name it "
                f"something.odt or pass format")
        if wanted in EXPORT_ONLY_FORMATS:
            raise AddressError(
                f"{wanted} is a format to export to, not one a document can "
                f"live in — export_document writes a copy and leaves the "
                f"document where it is")
        if wanted not in WRITER_SAVE_FILTERS:
            raise AddressError(
                f"nothing here knows how to save {wanted}; the formats are "
                f"{', '.join(sorted(WRITER_SAVE_FILTERS))}")
        return WRITER_SAVE_FILTERS[wanted], wanted

    def _store_as(self, doc: Any, path: str, document_format: Optional[str],
                  overwrite: bool) -> Dict[str, Any]:
        """Write the document to `path` and leave it living there"""
        target = os.path.abspath(os.path.expanduser(path))
        directory = os.path.dirname(target)
        if not os.path.isdir(directory):
            raise AddressError(f"there is no directory {directory} to save into")
        if os.path.exists(target) and not overwrite:
            raise AddressError(f"{target} exists already; pass overwrite=true "
                               f"to write over it")
        filter_name, wanted = self._save_filter(target, document_format)

        name = PropertyValue()
        name.Name, name.Value = "FilterName", filter_name
        over = PropertyValue()
        over.Name, over.Value = "Overwrite", bool(overwrite)
        doc.storeAsURL(_file_url(target), (name, over))
        return {"path": target, "url": _get_document_url(doc),
                "format": wanted, "filter": filter_name}

    def save_document(self, doc: Any = None, file_path: Optional[str] = None,
                      document_format: Optional[str] = None,
                      overwrite: bool = False) -> Dict[str, Any]:
        """
        Save a document, either where it lives or under a new name

        With a `file_path` this is Save As: the document is written there and
        goes on living there, in the format the extension names — or the one
        `document_format` names. Without a path it is saved where it already
        is, and a document that has never been saved is refused rather than
        written somewhere guessed.
        """
        if doc is None:
            doc = self.get_active_document()
        if not doc:
            return {"success": False, "error": "No document to save"}
        if _is_readonly(doc):
            return {"success": False,
                    "error": "The document is read-only, so it cannot be saved"}

        try:
            if file_path:
                was = _get_document_url(doc)
                written = self._store_as(doc, file_path, document_format,
                                         overwrite)
                logger.info(f"Saved document as {written['path']}")
                return {"success": True, "saved_as": written["path"],
                        "url": written["url"], "format": written["format"],
                        "filter": written["filter"], "was": was or None,
                        "lives_here_now": True}

            if not doc.hasLocation():
                return {"success": False,
                        "error": "This document has never been saved, so "
                                 "there is nowhere to save it; give a "
                                 "file_path and it will be saved there"}
            doc.store()
            logger.info("Saved document where it lives")
            return {"success": True, "saved_as": _document_path(doc),
                    "url": _get_document_url(doc), "lives_here_now": True}
        except AddressError as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            logger.error(f"Failed to save document: {e}")
            return {"success": False, "error": str(e)}

    def close_document(self, doc: Any = None, unsaved: Optional[str] = None,
                       ) -> Dict[str, Any]:
        """
        Close a document, having decided what happens to unsaved changes

        close(True) closes a modified document without a murmur and the
        changes are gone — measured — so a document with unsaved changes is
        refused unless `unsaved` says "save" or "discard". Nothing is thrown
        away because a caller forgot to think about it.
        """
        if doc is None:
            doc = self.get_active_document()
        if not doc:
            return {"success": False, "error": "No document to close"}

        title = _get_property(doc, "Title", "") or ""
        url = _get_document_url(doc)
        path = _document_path(doc)
        modified = bool(_get_property_call(doc, "isModified", False))

        if unsaved is not None and unsaved not in ("save", "discard"):
            return {"success": False,
                    "error": f'unsaved must be "save" or "discard", got '
                             f'{unsaved!r}'}

        saved = False
        if modified:
            if unsaved is None:
                return {"success": False, "modified": True, "title": title,
                        "url": url or None,
                        "error": "This document has changes that are not "
                                 "saved. Closing it would lose them, so say "
                                 'what to do: unsaved="save" to save them '
                                 'first, or unsaved="discard" to let them go.'}
            if unsaved == "save":
                if not doc.hasLocation():
                    return {"success": False, "modified": True,
                            "error": "This document has never been saved, so "
                                     "its changes cannot be saved on the way "
                                     "out; save_document with a file_path "
                                     "first, or close with "
                                     'unsaved="discard"'}
                try:
                    doc.store()
                    saved = True
                except Exception as e:
                    logger.error(f"Could not save before closing: {e}")
                    return {"success": False,
                            "error": f"could not save it, so it was left open: "
                                     f"{e}"}
            else:
                try:
                    doc.setModified(False)
                except Exception as e:
                    logger.info(f"Could not clear the modified flag: {e}")

        # Anchors are cursors into this document; once it closes they are
        # disposed proxies, so they go with it rather than waiting to be
        # asked and throwing.
        let_go = self._drop_document_anchors(doc)

        try:
            doc.close(True)
        except Exception as e:
            logger.error(f"Could not close the document: {e}")
            return {"success": False,
                    "error": f"LibreOffice would not close it: {e}"}

        remaining = []
        for other in self.open_documents():
            remaining.append(_document_path(other)
                             or _get_property(other, "Title", "")
                             or "(unsaved)")
        return {"success": True, "closed": title, "path": path,
                "url": url or None, "changes_saved": saved,
                "changes_discarded": modified and not saved,
                "anchors_let_go": let_go,
                "documents_still_open": remaining}

    def rename_document(self, new_name: str, doc: Any = None,
                        delete_original: bool = False,
                        overwrite: bool = False) -> Dict[str, Any]:
        """
        Give a document another name, and say what became of the old file

        UNO has no rename: storeAsURL writes the document under the new name
        and the old file stays where it was — measured, both files there
        afterwards. So the old one is kept unless `delete_original` says
        otherwise, and this reports which happened rather than leaving it to
        be guessed.

        `new_name` may be a bare name, in which case the document keeps its
        directory, and may leave the extension off, in which case it keeps
        the one it has.
        """
        if doc is None:
            doc = self.get_active_document()
        if not doc:
            return {"success": False, "error": "No document to rename"}
        if not isinstance(new_name, str) or not new_name.strip():
            return {"success": False, "error": "new_name must be a name"}
        if _is_readonly(doc):
            return {"success": False,
                    "error": "The document is read-only, so it cannot be "
                             "written under another name"}

        original = _document_path(doc)
        if not original:
            return {"success": False,
                    "error": "This document has never been saved, so it has "
                             "no name to change; save_document with a "
                             "file_path gives it one"}

        wanted = os.path.expanduser(new_name.strip())
        if not os.path.dirname(wanted):
            wanted = os.path.join(os.path.dirname(original), wanted)
        if not os.path.splitext(wanted)[1]:
            wanted += os.path.splitext(original)[1]
        wanted = os.path.abspath(wanted)

        if wanted == original:
            return {"success": False,
                    "error": f"the document is already called "
                             f"{os.path.basename(original)}"}

        try:
            written = self._store_as(doc, wanted, None, overwrite)
        except AddressError as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            logger.error(f"Could not write {wanted}: {e}")
            return {"success": False, "error": str(e)}

        removed = False
        if delete_original:
            try:
                os.unlink(original)
                removed = True
            except OSError as e:
                logger.error(f"Could not remove {original}: {e}")
                return {"success": True, "renamed_to": written["path"],
                        "url": written["url"], "was": original,
                        "original_kept": True,
                        "warning": f"the document is now {written['path']}, "
                                   f"but the old file could not be removed: "
                                   f"{e}"}
            # The lock file LibreOffice left beside the old name is stale now.
            lock = os.path.join(os.path.dirname(original),
                                f".~lock.{os.path.basename(original)}#")
            if os.path.exists(lock):
                try:
                    os.unlink(lock)
                except OSError as e:
                    logger.info(f"Could not remove {lock}: {e}")

        result = {"success": True, "renamed_to": written["path"],
                  "url": written["url"], "was": original,
                  "format": written["format"],
                  "original_kept": not removed}
        if not removed:
            result["note"] = (f"the old file is still at {original}; pass "
                              f"delete_original=true to have it removed")
        if (os.path.splitext(original)[1].lower()
                != os.path.splitext(written["path"])[1].lower()):
            result["format_changed"] = True
        return result

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
