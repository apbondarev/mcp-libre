"""The tools for writing styles: making, changing, renaming, replacing."""

from typing import Any, Dict, Optional

FAMILY = ("Which kind of style: \"paragraph\" or \"character\". The other "
          "families are read-only here — a page style is written with "
          "set_page_layout_live and the header tools")

PROPERTIES = ("What the style says, by name: bold, italic, underline, "
              "font_size, font_name, color, background_color, language, and "
              "for a paragraph style alignment, space_above_pt, "
              "space_below_pt, indent_left_mm, indent_right_mm, "
              "first_line_indent_mm, keep_with_next")


class StyleWritingTools:
    """Part of LibreOfficeMCPServer — see mcp_server.py."""

    def _register_stylewriting(self):
        """The tools of this part, as clients see them."""
        self.tools["create_style_live"] = {
            "description": "Make a new paragraph or character style, on its own or copied from one the document already has. describe_style_live reads a style; this writes one, which is what \"make this document use our house styles\" needs",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The new style's name"
                    },
                    "family": {
                        "type": "string",
                        "enum": ["paragraph", "character"],
                        "description": FAMILY,
                        "default": "paragraph"
                    },
                    "based_on": {
                        "type": "string",
                        "description": "The style it inherits from, e.g. \"Preformatted Text\""
                    },
                    "from_style": {
                        "type": "string",
                        "description": "Copy what that style sets itself — the properties describe_style_live reports under set_here — and then change what you like"
                    },
                    "next_style": {
                        "type": "string",
                        "description": "The style the paragraph after this one takes"
                    },
                    "properties": {
                        "type": "object",
                        "description": PROPERTIES
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                },
                "required": ["name"]
            },
            "handler": self.create_style_live
        }

        self.tools["update_style_live"] = {
            "description": "Change what a style says; every paragraph or run wearing it follows at once. A built-in style can be changed like any other — only removing one is refused",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The style's name, from list_styles_live"
                    },
                    "family": {
                        "type": "string",
                        "enum": ["paragraph", "character"],
                        "description": FAMILY,
                        "default": "paragraph"
                    },
                    "properties": {
                        "type": "object",
                        "description": PROPERTIES
                    },
                    "based_on": {
                        "type": "string",
                        "description": "Change what it inherits from"
                    },
                    "next_style": {
                        "type": "string",
                        "description": "Change the style the paragraph after it takes"
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                },
                "required": ["name"]
            },
            "handler": self.update_style_live
        }

        self.tools["rename_style_live"] = {
            "description": "Give a style another name — the text wearing it follows by itself. Only a style this document defines can be renamed; one of the office's own keeps the name it is known by, and create_style_live with from_style makes a copy you own",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "The style's name now"},
                    "new_name": {"type": "string", "description": "What to call it"},
                    "family": {
                        "type": "string",
                        "enum": ["paragraph", "character"],
                        "description": FAMILY,
                        "default": "paragraph"
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                },
                "required": ["name", "new_name"]
            },
            "handler": self.rename_style_live
        }

        self.tools["delete_style_live"] = {
            "description": "Remove a style this document defines. The text wearing it falls back to the style it was based on, unless replace_with says what it should wear instead. A built-in style is refused: UNO accepts removing one, leaves it exactly where it was, and says nothing",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "The style's name"},
                    "family": {
                        "type": "string",
                        "enum": ["paragraph", "character"],
                        "description": FAMILY,
                        "default": "paragraph"
                    },
                    "replace_with": {
                        "type": "string",
                        "description": "Put this style on the text first, instead of letting it fall back"
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                },
                "required": ["name"]
            },
            "handler": self.delete_style_live
        }

        self.tools["replace_style_live"] = {
            "description": "Put one style in the place of another, through the whole document or one part of it: every paragraph in the old style comes out in the new one and nothing else about the text changes. This is what \"use our house styles\" is made of",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "The style being replaced"},
                    "with_style": {"type": "string", "description": "The style to put in its place"},
                    "family": {
                        "type": "string",
                        "enum": ["paragraph", "character"],
                        "description": FAMILY,
                        "default": "paragraph"
                    },
                    "address": {
                        "type": "object",
                        "description": "Only inside this part of the document: {\"heading\": N}, {\"paragraph\": N}, {\"paragraph\": N, \"through\": M}, a range, or {\"selection\": true}",
                        "properties": {
                            "heading": {"type": "integer"},
                            "anchor": {"type": ["string", "object"]},
                            "paragraph": {"type": "integer"},
                            "through": {"type": "integer"},
                            "offset": {"type": "integer"},
                            "length": {"type": "integer"},
                            "selection": {"type": "boolean"}
                        }
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
                "required": ["name", "with_style"]
            },
            "handler": self.replace_style_live
        }

    def create_style_live(self, name: str, family: str = "paragraph",
                          based_on: Optional[str] = None,
                          from_style: Optional[str] = None,
                          next_style: Optional[str] = None,
                          properties: Optional[Dict[str, Any]] = None,
                          document: Optional[str] = None) -> Dict[str, Any]:
        """Make a new paragraph or character style"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.create_style(name, family=family,
                                            based_on=based_on,
                                            from_style=from_style,
                                            next_style=next_style,
                                            properties=properties, doc=doc)

    def update_style_live(self, name: str, family: str = "paragraph",
                          properties: Optional[Dict[str, Any]] = None,
                          based_on: Optional[str] = None,
                          next_style: Optional[str] = None,
                          document: Optional[str] = None) -> Dict[str, Any]:
        """Change what a style says"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.update_style(name, family=family,
                                            properties=properties,
                                            based_on=based_on,
                                            next_style=next_style, doc=doc)

    def rename_style_live(self, name: str, new_name: str,
                          family: str = "paragraph",
                          document: Optional[str] = None) -> Dict[str, Any]:
        """Give a style another name"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.rename_style(name, new_name, family=family,
                                            doc=doc)

    def delete_style_live(self, name: str, family: str = "paragraph",
                          replace_with: Optional[str] = None,
                          document: Optional[str] = None) -> Dict[str, Any]:
        """Remove a style this document defines"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.delete_style(name, family=family,
                                            replace_with=replace_with,
                                            doc=doc)

    def replace_style_live(self, name: str, with_style: str,
                           family: str = "paragraph", address: Any = None,
                           track_changes: Optional[bool] = None,
                           document: Optional[str] = None) -> Dict[str, Any]:
        """Put one style in the place of another"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.replace_style(name, with_style, family=family,
                                             address=address,
                                             track_changes=track_changes,
                                             doc=doc)
