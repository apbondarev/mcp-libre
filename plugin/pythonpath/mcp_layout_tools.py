"""The tools for page layout: the page itself, its breaks and its numbers."""

from typing import Any, Dict, Optional

PAGE_STYLE = ("Which page style; defaults to the one the caret is on. A "
              "document changes layout half way through by giving a "
              "paragraph a page break that switches style — see "
              "set_page_break_live")


class LayoutTools:
    """Part of LibreOfficeMCPServer — see mcp_server.py."""

    def _register_layout(self):
        """The tools of this part, as clients see them."""
        self.tools["get_page_layout_live"] = {
            "description": "The size, orientation, margins, columns and line numbering of a page style, in millimetres. Writer keeps these in 1/100 mm and rounds them — A4 reports 210.01 mm wide — so they are never exactly what was written",
            "parameters": {
                "type": "object",
                "properties": {
                    "page_style": {
                        "type": "string",
                        "description": PAGE_STYLE
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                }
            },
            "handler": self.get_page_layout_live
        }

        self.tools["set_page_layout_live"] = {
            "description": "Set the paper, orientation, margins, columns or gutter of a page style. Orientation is done properly: setting the landscape flag alone turns nothing in Writer — measured — so the width and height are swapped to match",
            "parameters": {
                "type": "object",
                "properties": {
                    "page_style": {
                        "type": "string",
                        "description": PAGE_STYLE
                    },
                    "paper": {
                        "type": "string",
                        "enum": ["a3", "a4", "a5", "letter", "legal"],
                        "description": "A named paper size, which sets the width and the height together"
                    },
                    "width_mm": {
                        "type": "number",
                        "description": "Page width in millimetres"
                    },
                    "height_mm": {
                        "type": "number",
                        "description": "Page height in millimetres"
                    },
                    "orientation": {
                        "type": "string",
                        "enum": ["portrait", "landscape"],
                        "description": "Which way round the page is; the size is swapped to match"
                    },
                    "margins_mm": {
                        "type": "object",
                        "description": "Any of top, bottom, left and right, in millimetres",
                        "properties": {
                            "top": {"type": "number"},
                            "bottom": {"type": "number"},
                            "left": {"type": "number"},
                            "right": {"type": "number"}
                        }
                    },
                    "columns": {
                        "type": "integer",
                        "description": "How many columns the page is set in, 1 to 99"
                    },
                    "gutter_mm": {
                        "type": "number",
                        "description": "Extra margin on the binding side, in millimetres"
                    },
                    "track_changes": {
                        "type": "boolean",
                        "description": "Record this edit as a tracked change. Omitted follows the document's own setting"
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                }
            },
            "handler": self.set_page_layout_live
        }

        self.tools["set_page_break_live"] = {
            "description": "Start a new page (or column) at a paragraph, take that break away with kind \"none\", or switch the page style at it — which is how a document turns landscape half way through. The break belongs to the paragraph that follows it",
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {
                        "type": "object",
                        "description": "The paragraph the break belongs to: {\"paragraph\": N}, {\"anchor\": \"a7f3c1\"} or {\"selection\": true}",
                        "properties": {
                            "anchor": {"type": "string"},
                            "paragraph": {"type": "integer"},
                            "offset": {"type": "integer"},
                            "length": {"type": "integer"},
                            "selection": {"type": "boolean"}
                        }
                    },
                    "kind": {
                        "type": "string",
                        "enum": ["page_before", "page_after", "column_before",
                                 "column_after", "none"],
                        "description": "Which break, or \"none\" to take one away",
                        "default": "page_before"
                    },
                    "page_style": {
                        "type": "string",
                        "description": "Switch to this page style at the break, e.g. \"Landscape\""
                    },
                    "page_number": {
                        "type": "integer",
                        "description": "Restart the page numbering at this number"
                    },
                    "track_changes": {
                        "type": "boolean",
                        "description": "Record this edit as a tracked change. Omitted follows the document's own setting"
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                },
                "required": ["address"]
            },
            "handler": self.set_page_break_live
        }

        self.tools["set_line_numbering_live"] = {
            "description": "Number the lines of a document, or stop numbering them — the numbering a reviewer refers to by line. It belongs to the document rather than to a page style",
            "parameters": {
                "type": "object",
                "properties": {
                    "on": {
                        "type": "boolean",
                        "description": "Switch line numbering on or off"
                    },
                    "interval": {
                        "type": "integer",
                        "description": "How many lines between numbers; 1 numbers every line"
                    },
                    "restart_each_page": {
                        "type": "boolean",
                        "description": "Start again from 1 on every page"
                    },
                    "count_empty_lines": {
                        "type": "boolean",
                        "description": "Count empty lines as lines"
                    },
                    "distance_mm": {
                        "type": "number",
                        "description": "How far the numbers sit from the text, in millimetres"
                    },
                    "track_changes": {
                        "type": "boolean",
                        "description": "Record this edit as a tracked change. Omitted follows the document's own setting"
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                }
            },
            "handler": self.set_line_numbering_live
        }

    def get_page_layout_live(self, page_style: Optional[str] = None,
                             document: Optional[str] = None) -> Dict[str, Any]:
        """The size, margins and columns of a page style"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.get_page_layout(page_style=page_style, doc=doc)

    def set_page_layout_live(self, page_style: Optional[str] = None,
                             paper: Optional[str] = None,
                             width_mm: Optional[float] = None,
                             height_mm: Optional[float] = None,
                             orientation: Optional[str] = None,
                             margins_mm: Optional[Dict[str, Any]] = None,
                             columns: Optional[int] = None,
                             gutter_mm: Optional[float] = None,
                             track_changes: Optional[bool] = None,
                             document: Optional[str] = None) -> Dict[str, Any]:
        """Set the paper, orientation, margins or columns of a page style"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.set_page_layout(
            page_style=page_style, paper=paper, width_mm=width_mm,
            height_mm=height_mm, orientation=orientation,
            margins_mm=margins_mm, columns=columns, gutter_mm=gutter_mm,
            track_changes=track_changes, doc=doc)

    def set_page_break_live(self, address: Any, kind: str = "page_before",
                            page_style: Optional[str] = None,
                            page_number: Optional[int] = None,
                            track_changes: Optional[bool] = None,
                            document: Optional[str] = None) -> Dict[str, Any]:
        """Start a new page at a paragraph, or take that break away"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.set_page_break(address, kind=kind,
                                              page_style=page_style,
                                              page_number=page_number,
                                              track_changes=track_changes,
                                              doc=doc)

    def set_line_numbering_live(self, on: Optional[bool] = None,
                                interval: Optional[int] = None,
                                restart_each_page: Optional[bool] = None,
                                count_empty_lines: Optional[bool] = None,
                                distance_mm: Optional[float] = None,
                                track_changes: Optional[bool] = None,
                                document: Optional[str] = None
                                ) -> Dict[str, Any]:
        """Number the lines of a document, or stop"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.set_line_numbering(
            on=on, interval=interval, restart_each_page=restart_each_page,
            count_empty_lines=count_empty_lines, distance_mm=distance_mm,
            track_changes=track_changes, doc=doc)
