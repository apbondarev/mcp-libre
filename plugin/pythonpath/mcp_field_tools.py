"""The tools for fields: the bits of a document that write themselves."""

from typing import Any, Dict, Optional

SCOPE = ("Which fields: omit for the whole document, {\"heading\": N} for a "
         "section, {\"paragraph\": N} for one paragraph, {\"paragraph\": N, "
         "\"through\": M} for a block, {\"paragraph\": N, \"offset\": K, "
         "\"length\": L} for a range, or {\"selection\": true}")

DOCUMENT = ("URL of the document to act on, from list_open_documents; "
            "defaults to the active document")

ADDRESS_PARTS = {
    "anchor": {"type": ["string", "object"]},
    "heading": {"type": "integer"},
    "paragraph": {"type": "integer"},
    "through": {"type": "integer"},
    "offset": {"type": "integer"},
    "length": {"type": "integer"},
    "selection": {"type": "boolean"},
}


class FieldTools:
    """Part of LibreOfficeMCPServer — see mcp_server.py."""

    def _register_field(self):
        """The tools of this part, as clients see them."""
        self.tools["list_fields_live"] = {
            "description": "List the fields of a document — the bits that write themselves: a date, a page number, the document's title. Each comes back with what it shows now, the command Writer names it by, and the address of the text it sits in. Read this before rewriting a paragraph: a field carries the text it shows, so its run looks like ordinary text and a rewrite destroys the field and leaves the text behind. Comments are fields too and are left out; list_comments_live is for those",
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {"type": "object", "description": SCOPE,
                                "properties": ADDRESS_PARTS},
                    "number": {
                        "type": "boolean",
                        "description": "Work out each field's paragraph and offset as well. A field's offset can only come from walking the portions of every paragraph — 14s for the 1536 fields of a real guide — so it is off unless a human needs to be shown where things are",
                        "default": False
                    },
                    "document": {"type": "string", "description": DOCUMENT}
                }
            },
            "handler": self.list_fields_live
        }

        self.tools["insert_field_live"] = {
            "description": "Put a field where an address points, so the document keeps that bit up to date itself: a date that follows the calendar, the page number on the page it is printed on, the title from the document's properties. Over a range the field takes the place of the text; at a caret it goes in beside it",
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {
                        "type": "object",
                        "description": "Where it goes: {\"paragraph\": N, \"offset\": K} for a caret, {\"paragraph\": N, \"offset\": K, \"length\": L} to put it in place of that text, {\"selection\": true}, or an anchor",
                        "properties": ADDRESS_PARTS
                    },
                    "kind": {
                        "type": "string",
                        "enum": ["date", "time", "page_number", "page_count",
                                 "title", "subject", "author", "file_name"],
                        "description": "What the field shows. date and time follow the clock unless fixed says otherwise; page_number is the page it is printed on; title and subject come from the document's properties"
                    },
                    "fixed": {
                        "type": "boolean",
                        "description": "For a date or a time: keep what it says now instead of following the clock",
                        "default": False
                    },
                    "track_changes": {
                        "type": "boolean",
                        "description": "Omit to follow the document's own setting; true records this edit, false refuses to record it"
                    },
                    "document": {"type": "string", "description": DOCUMENT}
                },
                "required": ["address", "kind"]
            },
            "handler": self.insert_field_live
        }

        self.tools["update_fields_live"] = {
            "description": "Make every field in the document redraw itself, as F9 does in Writer — after a page count changes, a title is edited, or a document is opened somewhere the date has moved on. The result says how many say something different than they did",
            "parameters": {
                "type": "object",
                "properties": {
                    "document": {"type": "string", "description": DOCUMENT}
                }
            },
            "handler": self.update_fields_live
        }

        self.tools["delete_field_live"] = {
            "description": "Take a field away, leaving the text around it. A field has no id of its own, so it is named by where it sits: an address covering exactly one field is removed, and one covering several is refused with their addresses so the right one can be named",
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {
                        "type": "object",
                        "description": "Where the field is, as list_fields_live reports it",
                        "properties": ADDRESS_PARTS
                    },
                    "document": {"type": "string", "description": DOCUMENT}
                },
                "required": ["address"]
            },
            "handler": self.delete_field_live
        }

    def list_fields_live(self, address: Any = None, number: bool = False,
                         document: Optional[str] = None) -> Dict[str, Any]:
        """The fields of a document, with what each one shows"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.list_fields(address=address, number=number,
                                           doc=doc)

    def insert_field_live(self, address: Any, kind: str, fixed: bool = False,
                          track_changes: Optional[bool] = None,
                          document: Optional[str] = None) -> Dict[str, Any]:
        """Put a field where an address points"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.insert_field(address, kind, fixed=fixed,
                                            track_changes=track_changes,
                                            doc=doc)

    def update_fields_live(self,
                           document: Optional[str] = None) -> Dict[str, Any]:
        """Make every field redraw itself"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.update_fields(doc=doc)

    def delete_field_live(self, address: Any,
                          document: Optional[str] = None) -> Dict[str, Any]:
        """Take a field away, leaving the text around it"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.delete_field(address, doc=doc)
