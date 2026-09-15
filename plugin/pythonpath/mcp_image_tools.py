"""The tools for pictures, and for a picture of a page."""

from typing import Any, Dict, Optional


class ImageTools:
    """Part of LibreOfficeMCPServer — see mcp_server.py."""

    def _register_image(self):
        """The tools of this part, as clients see them."""
        self.tools["list_images_live"] = {
            "description": "List the pictures of the active Writer document — or of one section, paragraph, range or the selection, which reports the picture the reader has selected — with the address of the anchor of each, the text it is anchored to, its size, its alternative text and whether it sits inline in the text. Use it to tell whether a selection holds a picture before rewriting the text, since replacing text that an inline picture sits in destroys the picture",
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {
                        "type": "object",
                        "description": "Which pictures: omit for the whole document, {\"heading\": N} for a section, {\"paragraph\": N} for one paragraph, {\"paragraph\": N, \"through\": M} for a block, {\"paragraph\": N, \"offset\": K, \"length\": L} for a range, or {\"selection\": true} — body text, not a table cell. An anchor from anchor or from find_text/read_paragraphs with anchors: true can be given instead, as {\"anchor\": \"a7f3c1\"} — it keeps pointing at the same text after edits have renumbered the paragraphs",
                        "properties": {
                            "heading": {"type": "integer"},
                            "anchor": {"type": "string"},
                            "paragraph": {"type": "integer"},
                            "offset": {"type": "integer"},
                            "length": {"type": "integer"},
                            "selection": {"type": "boolean"}
                        }
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                }
            },
            "handler": self.list_images_live
        }

        self.tools["export_image_live"] = {
            "description": "Write one of the document's pictures to a file and report where it went, its size in bytes and its size in pixels. Leave the name out to write the picture the reader has selected — selecting a picture in Writer leaves no text selection, so this is how \"save the selected picture\" is answered. With inline=true the picture also comes back in the reply, so it can be looked at directly",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The picture's name, as list_images_live reports it. Omit it to write the selected picture"
                    },
                    "path": {
                        "type": "string",
                        "description": "Where to write it; defaults to a file in the temporary directory named after the picture"
                    },
                    "format": {
                        "type": "string",
                        "description": "png, jpeg, gif, tiff, bmp, or \"original\" for the picture's own format",
                        "default": "png"
                    },
                    "inline": {
                        "type": "boolean",
                        "description": "Also return the picture itself in the reply, so it can be seen rather than only saved. Refused for a picture over 4 MB, which is then only written to the file",
                        "default": False
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                },
            },
            "handler": self.export_image_live
        }

        self.tools["render_page_live"] = {
            "description": "Render one page of the active Writer document as a picture and hand it back, so the layout can be looked at: fonts, spacing, borders, tables and pictures, with changes that have not been saved included. It is rendered by LibreOffice itself and shows the page as it prints — the spell checker's red underlines, the caret and the text boundary marks belong to Writer's window, not to the page. Give a page number, or an address to render the page that text is on, or neither for the page the reader is looking at",
            "parameters": {
                "type": "object",
                "properties": {
                    "page": {
                        "type": "integer",
                        "description": "Which page, counting from 1. Omit to render the page the cursor is on"
                    },
                    "address": {
                        "type": "object",
                        "description": "Render the page this text is on instead: {\"paragraph\": N}, {\"paragraph\": N, \"through\": M} for a block of whole paragraphs, {\"paragraph\": N, \"offset\": K, \"length\": L} for part of one, {\"table\": \"Table1\", \"cell\": \"A2\"} for a table cell, or {\"selection\": true}. An anchor from anchor or from find_text/read_paragraphs with anchors: true can be given instead, as {\"anchor\": \"a7f3c1\"} — it keeps pointing at the same text after edits have renumbered the paragraphs",
                        "properties": {
                            "anchor": {"type": "string"},
                            "paragraph": {"type": "integer"},
                            "offset": {"type": "integer"},
                            "length": {"type": "integer"},
                            "selection": {"type": "boolean"}
                        }
                    },
                    "dpi": {
                        "type": "integer",
                        "description": "Resolution, 20 to 300. 110 reads well; 200 shows fine detail and costs four times as much",
                        "default": 110
                    },
                    "path": {
                        "type": "string",
                        "description": "Where to write the picture; defaults to a file in the temporary directory"
                    },
                    "inline": {
                        "type": "boolean",
                        "description": "Return the picture in the reply as well, so it can be looked at rather than only saved",
                        "default": True
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                }
            },
            "handler": self.render_page_live
        }

    def list_images_live(self, address: Any = None,
                         document: Optional[str] = None) -> Dict[str, Any]:
        """List the pictures of a Writer document with their anchors"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.list_images(address=address, doc=doc)

    def export_image_live(self, name: Optional[str] = None,
                          path: Optional[str] = None,
                          format: str = "png", inline: bool = False,
                          document: Optional[str] = None) -> Dict[str, Any]:
        """Write a picture to a file, and hand back its bytes if asked"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.export_image(name, path=path,
                                            image_format=format,
                                            inline=inline, doc=doc)

    def render_page_live(self, page: Optional[int] = None, address: Any = None,
                         dpi: int = 110, path: Optional[str] = None,
                         inline: bool = True,
                         document: Optional[str] = None) -> Dict[str, Any]:
        """Render one page as a picture, laid out as it would print"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.render_page(page=page, address=address, dpi=dpi,
                                           path=path, inline=inline, doc=doc)
