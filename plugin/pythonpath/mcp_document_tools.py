"""The tools for documents themselves: making, saving, closing, renaming."""

from typing import Any, Dict, Optional


class DocumentTools:
    """Part of LibreOfficeMCPServer — see mcp_server.py."""

    def _register_document(self):
        """The tools of this part, as clients see them."""
        # Document creation tools
        self.tools["create_document_live"] = {
            "description": "Create a new document in LibreOffice",
            "parameters": {
                "type": "object",
                "properties": {
                    "doc_type": {
                        "type": "string",
                        "enum": ["writer", "calc", "impress", "draw"],
                        "description": "Type of document to create",
                        "default": "writer"
                    }
                }
            },
            "handler": self.create_document_live
        }

        # Document info tools
        self.tools["get_document_info_live"] = {
            "description": "Get information about the currently active document, including whether changes are being recorded (track_changes) and how many recorded changes await acceptance (tracked_changes)",
            "parameters": {
                "type": "object",
                "properties": {}
            },
            "handler": self.get_document_info_live
        }

        # Document saving tools
        self.tools["save_document_live"] = {
            "description": "Save a document — where it already lives, or under a new name, which is Save As: the document is written there and goes on living there. The format comes from the extension of the name (odt, docx, doc, rtf, txt, html), so a name nothing here can write is refused rather than saved as ODF under a misleading extension. A document that has never been saved needs a file_path",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Where to save it. Omit to save it where it already lives"
                    },
                    "format": {
                        "type": "string",
                        "description": "The format to write, when the extension does not say it: odt, docx, doc, rtf, txt, html. PDF is an export — use export_document_live, which writes a copy and leaves the document where it is"
                    },
                    "overwrite": {
                        "type": "boolean",
                        "description": "Write over a file that is already there",
                        "default": False
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                }
            },
            "handler": self.save_document_live
        }

        self.tools["close_document_live"] = {
            "description": "Close a document. A document with changes that are not saved is refused unless unsaved says what to do with them — closing discards them silently otherwise — and the result says whether they were saved or let go, and what is still open",
            "parameters": {
                "type": "object",
                "properties": {
                    "unsaved": {
                        "type": "string",
                        "enum": ["save", "discard"],
                        "description": "What to do with changes that are not saved: save them before closing, or let them go. Needed only when there are any"
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to close, from list_open_documents; defaults to the active document"
                    }
                }
            },
            "handler": self.close_document_live
        }

        self.tools["rename_document_live"] = {
            "description": "Give a document another name. UNO has no rename, so the document is written under the new name and goes on living there, while the old file stays where it was unless delete_original says to remove it — the result says which happened. A bare name keeps the document's directory, and leaving the extension off keeps the format it has",
            "parameters": {
                "type": "object",
                "properties": {
                    "new_name": {
                        "type": "string",
                        "description": "The new name, e.g. \"GraphQL Guide.odt\", or a whole path"
                    },
                    "delete_original": {
                        "type": "boolean",
                        "description": "Remove the file under the old name, making this a rename rather than a copy",
                        "default": False
                    },
                    "overwrite": {
                        "type": "boolean",
                        "description": "Write over a file that already has the new name",
                        "default": False
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to rename, from list_open_documents; defaults to the active document"
                    }
                },
                "required": ["new_name"]
            },
            "handler": self.rename_document_live
        }

        # Document export tools
        self.tools["export_document_live"] = {
            "description": "Export the currently active document to a different format",
            "parameters": {
                "type": "object",
                "properties": {
                    "export_format": {
                        "type": "string",
                        "enum": ["pdf", "docx", "doc", "odt", "txt", "rtf", "html"],
                        "description": "Format to export to"
                    },
                    "file_path": {
                        "type": "string",
                        "description": "Path to export document to"
                    }
                },
                "required": ["export_format", "file_path"]
            },
            "handler": self.export_document_live
        }

        # Document list tools
        self.tools["list_open_documents"] = {
            "description": "List all currently open documents in LibreOffice",
            "parameters": {
                "type": "object",
                "properties": {}
            },
            "handler": self.list_open_documents
        }

    # Tool handler methods
    
    def create_document_live(self, doc_type: str = "writer") -> Dict[str, Any]:
        """Create a new document in LibreOffice"""
        try:
            doc = self.uno_bridge.create_document(doc_type)
            doc_info = self.uno_bridge.get_document_info(doc)
            
            return {
                "success": True,
                "message": f"Created new {doc_type} document",
                "document_info": doc_info
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_document_info_live(self) -> Dict[str, Any]:
        """Get information about the currently active document"""
        doc_info = self.uno_bridge.get_document_info()
        if "error" in doc_info:
            return {"success": False, **doc_info}
        else:
            return {"success": True, "document_info": doc_info}

    def save_document_live(self, file_path: Optional[str] = None,
                           format: Optional[str] = None,
                           overwrite: bool = False,
                           document: Optional[str] = None) -> Dict[str, Any]:
        """Save a document where it lives, or under a new name"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.save_document(doc=doc, file_path=file_path,
                                             document_format=format,
                                             overwrite=overwrite)

    def close_document_live(self, unsaved: Optional[str] = None,
                            document: Optional[str] = None) -> Dict[str, Any]:
        """Close a document, having decided about unsaved changes"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.close_document(doc=doc, unsaved=unsaved)

    def rename_document_live(self, new_name: str,
                             delete_original: bool = False,
                             overwrite: bool = False,
                             document: Optional[str] = None) -> Dict[str, Any]:
        """Give a document another name"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.rename_document(new_name, doc=doc,
                                               delete_original=delete_original,
                                               overwrite=overwrite)

    def export_document_live(self, export_format: str, file_path: str) -> Dict[str, Any]:
        """Export the currently active document"""
        return self.uno_bridge.export_document(export_format, file_path)

    def list_open_documents(self) -> Dict[str, Any]:
        """List all open documents in LibreOffice"""
        try:
            # Components, not frames: a frame can belong to a dialog or the
            # Start Center, which used to be listed as a document titled
            # "Unknown" with no URL and type "unknown".
            documents = [self.uno_bridge.get_document_info(doc)
                         for doc in self.uno_bridge.open_documents()]

            return {
                "success": True,
                "documents": documents,
                "count": len(documents)
            }

        except Exception as e:
            return {"success": False, "error": str(e)}
