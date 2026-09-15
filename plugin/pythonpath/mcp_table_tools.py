"""The tools for tables: listing them, reading them, dressing them."""

from typing import Any, Dict, Optional


class TableTools:
    """Part of LibreOfficeMCPServer — see mcp_server.py."""

    def _register_table(self):
        """The tools of this part, as clients see them."""
        # Pictures
        # Tables
        self.tools["create_table_live"] = {
            "description": "Put a new table into the text and fill it. The table goes in before the paragraph the address points at. With replace=true the paragraphs that address covers are cleared away afterwards, which is how a query and its response become a table in one call — and which is refused when those paragraphs hold comments, pictures or a table, unless flatten says to go ahead",
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {
                        "type": "object",
                        "description": "Where it goes: {\"paragraph\": N} puts the table before that paragraph, {\"paragraph\": N, \"through\": M} covers a whole block of paragraphs (which is what replace=true then clears away), {\"selection\": true} before the selected text. Omit for the selection. An anchor from anchor or from find_text/read_paragraphs with anchors: true can be given instead, as {\"anchor\": \"a7f3c1\"} — it keeps pointing at the same text after edits have renumbered the paragraphs",
                        "properties": {
                            "anchor": {"type": "string"},
                            "paragraph": {"type": "integer"},
                            "through": {"type": "integer"},
                            "offset": {"type": "integer"},
                            "length": {"type": "integer"},
                            "selection": {"type": "boolean"}
                        }
                    },
                    "rows": {"type": "integer", "description": "How many rows", "default": 2},
                    "columns": {"type": "integer", "description": "How many columns", "default": 2},
                    "cells": {
                        "type": "array",
                        "description": "The text to fill it with, row by row: [[\"Operation\", \"Response\"], [\"{ hero }\", \"{ ... }\"]]. Fewer rows or cells than the table has is fine",
                        "items": {"type": "array", "items": {"type": "string"}}
                    },
                    "name": {
                        "type": "string",
                        "description": "A name of your own for the table; Writer names it Table1, Table2 … otherwise"
                    },
                    "header_rows": {
                        "type": "integer",
                        "description": "How many rows at the top are the heading"
                    },
                    "repeat_heading": {
                        "type": "boolean",
                        "description": "Repeat those heading rows when the table runs onto another page"
                    },
                    "replace": {
                        "type": "boolean",
                        "description": "Clear away the paragraphs the address covers, so the table stands in their place",
                        "default": False
                    },
                    "flatten": {
                        "type": "boolean",
                        "description": "Accept losing what those paragraphs hold — comments, pictures, a table",
                        "default": False
                    },
                    "track_changes": {
                        "type": "boolean",
                        "description": "Record this edit as a tracked change, or refuse to record it. Omit to follow the document"
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                }
            },
            "handler": self.create_table_live
        }
        
        self.tools["delete_table_live"] = {
            "description": "Take a table out of the document, text and all. The result says what it held, so it is on the record and can be put back with create_table_live. With no name the table the caret is in is removed",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The table's name, from list_tables_live. Omit it for the table the caret is in"
                    },
                    "track_changes": {
                        "type": "boolean",
                        "description": "Record this edit as a tracked change, or refuse to record it. Omit to follow the document"
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                }
            },
            "handler": self.delete_table_live
        }
        
        self.tools["list_tables_live"] = {
            "description": "List the tables of the active Writer document: the name of each, its size in rows and columns, how many header rows it repeats, its width, whether any cells are merged, and how many body paragraphs come before it — addresses count paragraphs and skip tables, so that is where a table sits in the text. Also says which table and cell the caret is in, when it is in one",
            "parameters": {
                "type": "object",
                "properties": {
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                }
            },
            "handler": self.list_tables_live
        }

        self.tools["describe_table_live"] = {
            "description": "Report what a table looks like: its grid of borders with widths and colours, the padding in its cells, the background of the table and of every cell, the heading rows, the share of each column, and the paragraph styles the cells use — with runs=true, the coloured pieces of their text as well. It is the mirror of format_table_live: the values come back in the units that tool takes, so the look of one table can be read and put on another",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The table's name, from list_tables_live. Omit it for the table the caret is in"
                    },
                    "cells": {
                        "type": "boolean",
                        "description": "Describe every cell as well as the table",
                        "default": True
                    },
                    "runs": {
                        "type": "boolean",
                        "description": "Also return the formatted pieces of each cell's text, with their colours",
                        "default": False
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                }
            },
            "handler": self.describe_table_live
        }
        
        self.tools["read_table_live"] = {
            "description": "Read a table as a grid of cells with their text, or one cell of it. With no name the table the caret is in is read, which is what \"this table\" means — a caret in a cell belongs to no body paragraph, so the text tools report nothing there. Cell text keeps the line breaks of a cell holding several paragraphs, and a merged-away cell comes back as null",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The table's name, as list_tables_live reports it (e.g. \"Table1\"). Omit it to read the table the caret is in"
                    },
                    "cell": {
                        "type": "string",
                        "description": "Read only this cell, named as Writer names it: \"A1\", \"B2\""
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                }
            },
            "handler": self.read_table_live
        }

        self.tools["format_table_live"] = {
            "description": "Give a table a look: the grid of borders, the padding inside its cells, a background, header rows that repeat across pages, a paragraph style for the text in the cells, character formatting, and the width of each column. With no name the table the caret is in is formatted. The cell-level settings touch the cells named in \"cells\" — a list, a rectangle \"A1:B2\", \"row:2\", \"column:B\" — or every cell when that is left out",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The table's name, from list_tables_live. Omit it for the table the caret is in"
                    },
                    "cells": {
                        "description": "Which cells the cell settings touch: [\"A1\", \"B1\"], \"A1:B2\", \"row:2\", \"column:B\". Omit for every cell"
                    },
                    "border": {
                        "type": "boolean",
                        "description": "Draw the grid, or take it away with false"
                    },
                    "border_color": {
                        "type": "string",
                        "description": "Colour of the grid, e.g. \"#808080\"",
                        "default": "#808080"
                    },
                    "border_width": {
                        "type": "number",
                        "description": "Thickness of the grid in millimetres",
                        "default": 0.35
                    },
                    "inner_borders": {
                        "type": "boolean",
                        "description": "Draw the lines between the cells as well as the outline. Defaults to true when a border is asked for"
                    },
                    "padding_mm": {
                        "type": "number",
                        "description": "Space between a cell's border and its text, in millimetres"
                    },
                    "background_color": {
                        "type": "string",
                        "description": "Background of the cells, e.g. \"#F7F7F7\""
                    },
                    "header_rows": {
                        "type": "integer",
                        "description": "How many rows at the top are the heading"
                    },
                    "repeat_heading": {
                        "type": "boolean",
                        "description": "Repeat the heading rows when the table runs onto another page"
                    },
                    "header_background_color": {
                        "type": "string",
                        "description": "Background for the heading rows alone; needs header_rows"
                    },
                    "header_bold": {
                        "type": "boolean",
                        "description": "Make the heading rows bold; needs header_rows"
                    },
                    "paragraph_style": {
                        "type": "string",
                        "description": "Paragraph style for the text in the cells, e.g. \"Preformatted Text\" for code"
                    },
                    "bold": {"type": "boolean", "description": "Bold on or off"},
                    "italic": {"type": "boolean", "description": "Italic on or off"},
                    "font_name": {"type": "string", "description": "Font for the cells"},
                    "font_size": {"type": "number", "description": "Size in points"},
                    "color": {"type": "string", "description": "Text colour, e.g. \"#333333\""},
                    "column_widths_percent": {
                        "type": "array",
                        "items": {"type": "number"},
                        "description": "The share of the table each column takes, one number per column, adding up to 100"
                    },
                    "track_changes": {
                        "type": "boolean",
                        "description": "Record this edit as a tracked change, or refuse to record it. Omit to follow the document"
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                }
            },
            "handler": self.format_table_live
        }

    def format_table_live(self, name: Optional[str] = None, cells: Any = None,
                          border: Optional[bool] = None,
                          border_color: Any = "#808080",
                          border_width: float = 0.35,
                          inner_borders: Optional[bool] = None,
                          padding_mm: Optional[float] = None,
                          background_color: Any = None,
                          header_rows: Optional[int] = None,
                          repeat_heading: Optional[bool] = None,
                          header_background_color: Any = None,
                          header_bold: Optional[bool] = None,
                          paragraph_style: Optional[str] = None,
                          bold: Optional[bool] = None,
                          italic: Optional[bool] = None,
                          font_name: Optional[str] = None,
                          font_size: Optional[float] = None,
                          color: Any = None,
                          column_widths_percent: Any = None,
                          track_changes: Optional[bool] = None,
                          document: Optional[str] = None) -> Dict[str, Any]:
        """Give a table its look: grid, padding, header, cells, columns"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.format_table(
            name=name, cells=cells, border=border, border_color=border_color,
            border_width=border_width, inner_borders=inner_borders,
            padding_mm=padding_mm, background_color=background_color,
            header_rows=header_rows, repeat_heading=repeat_heading,
            header_background_color=header_background_color,
            header_bold=header_bold, paragraph_style=paragraph_style,
            bold=bold, italic=italic, font_name=font_name, font_size=font_size,
            color=color, column_widths_percent=column_widths_percent,
            track_changes=track_changes, doc=doc)

    def create_table_live(self, address: Any = None, rows: int = 2,
                          columns: int = 2, cells: Any = None,
                          name: Optional[str] = None,
                          header_rows: Optional[int] = None,
                          repeat_heading: Optional[bool] = None,
                          replace: bool = False, flatten: bool = False,
                          track_changes: Optional[bool] = None,
                          document: Optional[str] = None) -> Dict[str, Any]:
        """Put a new table into the text and fill it"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.create_table(
            address=address, rows=rows, columns=columns, cells=cells, name=name,
            header_rows=header_rows, repeat_heading=repeat_heading,
            replace=replace, flatten=flatten, track_changes=track_changes,
            doc=doc)

    def delete_table_live(self, name: Optional[str] = None,
                          track_changes: Optional[bool] = None,
                          document: Optional[str] = None) -> Dict[str, Any]:
        """Take a table out of the document"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.delete_table(name=name,
                                            track_changes=track_changes,
                                            doc=doc)

    def list_tables_live(self, document: Optional[str] = None) -> Dict[str, Any]:
        """List the tables of a Writer document"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.list_tables(doc=doc)

    def describe_table_live(self, name: Optional[str] = None,
                            cells: bool = True, runs: bool = False,
                            document: Optional[str] = None) -> Dict[str, Any]:
        """Report what a table looks like, in the units format_table takes"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.describe_table(name=name, cells=cells, runs=runs,
                                              doc=doc)

    def read_table_live(self, name: Optional[str] = None,
                        cell: Optional[str] = None,
                        document: Optional[str] = None) -> Dict[str, Any]:
        """Read a table as a grid, or one of its cells"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.read_table(name=name, cell=cell, doc=doc)
