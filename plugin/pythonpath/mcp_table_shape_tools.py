"""The tools that change a table's shape, and the order of its rows."""

from typing import Any, Dict, Optional


class TableShapeTools:
    """Part of LibreOfficeMCPServer — see mcp_server.py."""

    def _register_table_shape(self):
        """The tools of this part, as clients see them."""
        self.tools["insert_table_rows_live"] = {
            "description": "Put empty rows into a table. They go in before the row `at` names, so at=0 is the top and leaving it out adds them at the end",
            "parameters": {
                "type": "object",
                "properties": {
                    "at": {
                        "type": "integer",
                        "description": "The 0-based row the new ones go before; omit to add them at the end"
                    },
                    "count": {
                        "type": "integer",
                        "description": "How many to add",
                        "default": 1
                    },
                    "name": {
                        "type": "string",
                        "description": "Which table, as list_tables_live names it; omit to use the one the caret is in"
                    },
                    "track_changes": {
                        "type": "boolean",
                        "description": "Omit to follow the document's own setting; true records this edit, false refuses to record it"
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                }
            },
            "handler": self.insert_table_rows_live
        }

        self.tools["delete_table_rows_live"] = {
            "description": "Take rows out of a table. The text that went with them comes back in the result, so it can be put back if that was a mistake. A table cannot lose all its rows — use delete_table_live for that",
            "parameters": {
                "type": "object",
                "properties": {
                    "at": {
                        "type": "integer",
                        "description": "The 0-based first row to remove; omit to remove from the end"
                    },
                    "count": {
                        "type": "integer",
                        "description": "How many to remove",
                        "default": 1
                    },
                    "name": {
                        "type": "string",
                        "description": "Which table, as list_tables_live names it; omit to use the one the caret is in"
                    },
                    "track_changes": {
                        "type": "boolean",
                        "description": "Omit to follow the document's own setting; true records this edit, false refuses to record it"
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                }
            },
            "handler": self.delete_table_rows_live
        }

        self.tools["insert_table_columns_live"] = {
            "description": "Put empty columns into a table. They go in before the column `at` names, so at=0 is the top and leaving it out adds them at the end",
            "parameters": {
                "type": "object",
                "properties": {
                    "at": {
                        "type": "integer",
                        "description": "The 0-based column the new ones go before; omit to add them at the end"
                    },
                    "count": {
                        "type": "integer",
                        "description": "How many to add",
                        "default": 1
                    },
                    "name": {
                        "type": "string",
                        "description": "Which table, as list_tables_live names it; omit to use the one the caret is in"
                    },
                    "track_changes": {
                        "type": "boolean",
                        "description": "Omit to follow the document's own setting; true records this edit, false refuses to record it"
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                }
            },
            "handler": self.insert_table_columns_live
        }

        self.tools["delete_table_columns_live"] = {
            "description": "Take columns out of a table. The text that went with them comes back in the result, so it can be put back if that was a mistake. A table cannot lose all its columns — use delete_table_live for that",
            "parameters": {
                "type": "object",
                "properties": {
                    "at": {
                        "type": "integer",
                        "description": "The 0-based first column to remove; omit to remove from the end"
                    },
                    "count": {
                        "type": "integer",
                        "description": "How many to remove",
                        "default": 1
                    },
                    "name": {
                        "type": "string",
                        "description": "Which table, as list_tables_live names it; omit to use the one the caret is in"
                    },
                    "track_changes": {
                        "type": "boolean",
                        "description": "Omit to follow the document's own setting; true records this edit, false refuses to record it"
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                }
            },
            "handler": self.delete_table_columns_live
        }

        self.tools["merge_table_cells_live"] = {
            "description": "Make one cell out of a rectangle of them. The merged cell keeps every text that was in the rectangle, joined by line breaks, and the cells that went are gone from the grid — read_table_live reports them as null afterwards",
            "parameters": {
                "type": "object",
                "properties": {
                    "cells": {
                        "type": "string",
                        "description": "The rectangle to merge, as in \"A1:B2\" — or a whole \"row:1\" or \"column:B\""
                    },
                    "name": {
                        "type": "string",
                        "description": "Which table, as list_tables_live names it; omit to use the one the caret is in"
                    },
                    "track_changes": {
                        "type": "boolean",
                        "description": "Omit to follow the document's own setting; true records this edit, false refuses to record it"
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                },
                "required": ["cells"]
            },
            "handler": self.merge_table_cells_live
        }

        self.tools["split_table_cells_live"] = {
            "description": "Divide cells in two or more. direction says which way the dividing line runs: \"rows\" stacks the new cells one above another, \"columns\" puts them side by side",
            "parameters": {
                "type": "object",
                "properties": {
                    "cells": {
                        "type": "string",
                        "description": "Which cells: one name like \"A1\", a rectangle \"A1:B2\", a whole \"row:2\" or \"column:B\""
                    },
                    "into": {
                        "type": "integer",
                        "description": "How many cells each one becomes",
                        "default": 2
                    },
                    "direction": {
                        "type": "string",
                        "enum": ["rows", "columns"],
                        "description": "\"rows\" divides a cell by a horizontal line, \"columns\" by a vertical one",
                        "default": "rows"
                    },
                    "name": {
                        "type": "string",
                        "description": "Which table, as list_tables_live names it; omit to use the one the caret is in"
                    },
                    "track_changes": {
                        "type": "boolean",
                        "description": "Omit to follow the document's own setting; true records this edit, false refuses to record it"
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                },
                "required": ["cells"]
            },
            "handler": self.split_table_cells_live
        }

        self.tools["sort_table_live"] = {
            "description": "Put a table's rows in the order of one column. The heading rows stay where they are — the table's own HeaderRowCount unless header_rows says otherwise. This moves the text between cells: a cell's background and paragraph style stay where they are, so banding stays banded, but character formatting inside a moved cell is flattened, and a cell holding more than one formatted run is refused unless flatten says to accept that",
            "parameters": {
                "type": "object",
                "properties": {
                    "column": {
                        "type": ["string", "integer"],
                        "description": "Which column to sort by: a letter like \"B\" or a number from 1",
                        "default": 1
                    },
                    "descending": {
                        "type": "boolean",
                        "description": "Largest or last first",
                        "default": False
                    },
                    "numeric": {
                        "type": "boolean",
                        "description": "Compare the cells as numbers rather than as text, so 10 comes after 2. A cell that is not a number sorts after every cell that is",
                        "default": False
                    },
                    "header_rows": {
                        "type": "integer",
                        "description": "How many rows at the top to leave where they are; defaults to the table's own heading rows"
                    },
                    "flatten": {
                        "type": "boolean",
                        "description": "Sort even though a cell holds several formatted runs, which writing it back in its new place flattens",
                        "default": False
                    },
                    "name": {
                        "type": "string",
                        "description": "Which table, as list_tables_live names it; omit to use the one the caret is in"
                    },
                    "track_changes": {
                        "type": "boolean",
                        "description": "Omit to follow the document's own setting; true records this edit, false refuses to record it"
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                }
            },
            "handler": self.sort_table_live
        }

    def insert_table_rows_live(self, name: Optional[str] = None,
                                 at: Optional[int] = None, count: int = 1,
                                 track_changes: Optional[bool] = None,
                                 document: Optional[str] = None
                                 ) -> Dict[str, Any]:
        """Add whole rows of a table"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.insert_table_rows(
            name=name, at=at, count=count, track_changes=track_changes,
            doc=doc)

    def delete_table_rows_live(self, name: Optional[str] = None,
                                 at: Optional[int] = None, count: int = 1,
                                 track_changes: Optional[bool] = None,
                                 document: Optional[str] = None
                                 ) -> Dict[str, Any]:
        """Take away whole rows of a table"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.delete_table_rows(
            name=name, at=at, count=count, track_changes=track_changes,
            doc=doc)

    def insert_table_columns_live(self, name: Optional[str] = None,
                                 at: Optional[int] = None, count: int = 1,
                                 track_changes: Optional[bool] = None,
                                 document: Optional[str] = None
                                 ) -> Dict[str, Any]:
        """Add whole columns of a table"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.insert_table_columns(
            name=name, at=at, count=count, track_changes=track_changes,
            doc=doc)

    def delete_table_columns_live(self, name: Optional[str] = None,
                                 at: Optional[int] = None, count: int = 1,
                                 track_changes: Optional[bool] = None,
                                 document: Optional[str] = None
                                 ) -> Dict[str, Any]:
        """Take away whole columns of a table"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.delete_table_columns(
            name=name, at=at, count=count, track_changes=track_changes,
            doc=doc)

    def merge_table_cells_live(self, cells: Any, name: Optional[str] = None,
                               track_changes: Optional[bool] = None,
                               document: Optional[str] = None
                               ) -> Dict[str, Any]:
        """Make one cell out of a rectangle of them"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.merge_table_cells(
            cells, name=name, track_changes=track_changes, doc=doc)

    def split_table_cells_live(self, cells: Any, into: int = 2,
                               direction: str = "rows",
                               name: Optional[str] = None,
                               track_changes: Optional[bool] = None,
                               document: Optional[str] = None
                               ) -> Dict[str, Any]:
        """Divide cells in two or more"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.split_table_cells(
            cells, into=into, direction=direction, name=name,
            track_changes=track_changes, doc=doc)

    def sort_table_live(self, column: Any = 1, descending: bool = False,
                        numeric: bool = False,
                        header_rows: Optional[int] = None,
                        flatten: bool = False, name: Optional[str] = None,
                        track_changes: Optional[bool] = None,
                        document: Optional[str] = None) -> Dict[str, Any]:
        """Put a table's rows in the order of one column"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.sort_table(
            column=column, name=name, descending=descending, numeric=numeric,
            header_rows=header_rows, flatten=flatten,
            track_changes=track_changes, doc=doc)
