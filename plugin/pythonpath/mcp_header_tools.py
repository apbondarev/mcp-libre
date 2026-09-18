"""The tools for headers and footers: what every page carries."""

from typing import Any, Dict, Optional

WHICH = ("Which one: \"all\" for the header every page shares, \"left\" or "
         "\"right\" when the two sides differ (asking for one makes them "
         "differ), or \"first\" for a first page of its own")

PLACES = ("{page}, {pages}, {title}, {date}, {author} and {file} in the text "
          "become real fields, so \"Страница {page} из {pages}\" keeps "
          "counting as the document grows")


class HeaderTools:
    """Part of LibreOfficeMCPServer — see mcp_server.py."""

    def _register_header(self):
        """The tools of this part, as clients see them."""
        self.tools["list_headers_footers_live"] = {
            "description": "List the headers and footers of a Writer document, by page style, with the text each one carries. A running title lives here rather than in the text, which is why a translated document still shows the old one — nothing in the body says what a reader sees at the top of the page",
            "parameters": {
                "type": "object",
                "properties": {
                    "page_style": {
                        "type": "string",
                        "description": "Only this page style, e.g. \"Standard\" or \"First Page\". Omitted, the styles that carry something are listed, plus the one in use"
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                }
            },
            "handler": self.list_headers_footers_live
        }

        self.tools["set_header_live"] = {
            "description": "Write the header of a page style, switching it on if it is off. " + PLACES,
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "What the header says"
                    },
                    "page_style": {
                        "type": "string",
                        "description": "Which page style's header; defaults to the one the caret is on"
                    },
                    "which": {
                        "type": "string",
                        "enum": ["all", "left", "right", "first"],
                        "description": WHICH,
                        "default": "all"
                    },
                    "same_on_both_pages": {
                        "type": "boolean",
                        "description": "true gives every page the same header; false lets the left and right pages differ"
                    },
                    "same_on_the_first_page": {
                        "type": "boolean",
                        "description": "false gives the first page a header of its own"
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
                "required": ["text"]
            },
            "handler": self.set_header_live
        }

        self.tools["set_footer_live"] = {
            "description": "Write the footer of a page style, switching it on if it is off. This is where a page number belongs: " + PLACES,
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "What the footer says, e.g. \"Страница {page} из {pages}\""
                    },
                    "page_style": {
                        "type": "string",
                        "description": "Which page style's footer; defaults to the one the caret is on"
                    },
                    "which": {
                        "type": "string",
                        "enum": ["all", "left", "right", "first"],
                        "description": WHICH,
                        "default": "all"
                    },
                    "same_on_both_pages": {
                        "type": "boolean",
                        "description": "true gives every page the same footer; false lets the left and right pages differ"
                    },
                    "same_on_the_first_page": {
                        "type": "boolean",
                        "description": "false gives the first page a footer of its own"
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
                "required": ["text"]
            },
            "handler": self.set_footer_live
        }

        self.tools["remove_header_footer_live"] = {
            "description": "Switch a header or a footer off. This throws its text away — measured: turning one off and on again leaves it empty on both sides, since Writer keeps nothing — so what it said comes back in the result",
            "parameters": {
                "type": "object",
                "properties": {
                    "part": {
                        "type": "string",
                        "enum": ["header", "footer"],
                        "description": "Which of the two to switch off"
                    },
                    "page_style": {
                        "type": "string",
                        "description": "Which page style's; defaults to the one the caret is on"
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
                "required": ["part"]
            },
            "handler": self.remove_header_footer_live
        }

    def list_headers_footers_live(self, page_style: Optional[str] = None,
                                  document: Optional[str] = None
                                  ) -> Dict[str, Any]:
        """List the headers and footers by page style"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.list_headers_footers(page_style=page_style,
                                                    doc=doc)

    def set_header_live(self, text: str, page_style: Optional[str] = None,
                        which: str = "all",
                        same_on_both_pages: Optional[bool] = None,
                        same_on_the_first_page: Optional[bool] = None,
                        track_changes: Optional[bool] = None,
                        document: Optional[str] = None) -> Dict[str, Any]:
        """Write the header of a page style"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.set_header_footer(
            "header", text, page_style=page_style, which=which,
            same_on_both_pages=same_on_both_pages,
            same_on_the_first_page=same_on_the_first_page,
            track_changes=track_changes, doc=doc)

    def set_footer_live(self, text: str, page_style: Optional[str] = None,
                        which: str = "all",
                        same_on_both_pages: Optional[bool] = None,
                        same_on_the_first_page: Optional[bool] = None,
                        track_changes: Optional[bool] = None,
                        document: Optional[str] = None) -> Dict[str, Any]:
        """Write the footer of a page style"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.set_header_footer(
            "footer", text, page_style=page_style, which=which,
            same_on_both_pages=same_on_both_pages,
            same_on_the_first_page=same_on_the_first_page,
            track_changes=track_changes, doc=doc)

    def remove_header_footer_live(self, part: str,
                                  page_style: Optional[str] = None,
                                  track_changes: Optional[bool] = None,
                                  document: Optional[str] = None
                                  ) -> Dict[str, Any]:
        """Switch a header or a footer off"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.remove_header_footer(
            part, page_style=page_style, track_changes=track_changes, doc=doc)
