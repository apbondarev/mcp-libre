"""The tools for formulas: the maths a document carries as objects."""

from typing import Any, Dict, Optional

SCOPE = ("Which formulas: omit for the whole document, {\"heading\": N} for a "
         "section, {\"paragraph\": N}, {\"paragraph\": N, \"through\": M}, a "
         "range, or {\"selection\": true}")

DOCUMENT = ("URL of the document to act on, from list_open_documents; "
            "defaults to the active document")

STARMATH = ("The formula in StarMath, LibreOffice Math's own notation: "
            "\"{ frac { 1 } { 5 } }\" for a fifth, \"rho _ 1\" for ρ with a "
            "subscript, \"x ^ 2 + sqrt { y }\", \"a over b\", \"sum from { i = 1 } to n\"")

ADDRESS_PARTS = {
    "anchor": {"type": ["string", "object"]},
    "bookmark": {"type": "string"},
    "heading": {"type": "integer"},
    "paragraph": {"type": "integer"},
    "through": {"type": "integer"},
    "offset": {"type": "integer"},
    "length": {"type": "integer"},
    "selection": {"type": "boolean"},
}


class FormulaTools:
    """Part of LibreOfficeMCPServer — see mcp_server.py."""

    def _register_formula(self):
        """The tools of this part, as clients see them."""
        self.tools["list_formulas_live"] = {
            "description": "List the formulas of a document in reading order, each with its text in StarMath, its place and the words on either side of it. A formula is an object of its own, not text, so read_paragraphs and read_runs pass straight over it: \"the volume is  of the whole\" is all they show of \"the volume is 1/5 of the whole\". This is how to read what is missing there — `text_before` and `text_after` say which gap in the sentence each formula fills. Other embedded objects, charts and drawings, are not listed",
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {"type": "object", "description": SCOPE,
                                "properties": ADDRESS_PARTS},
                    "number": {
                        "type": "boolean",
                        "description": "Say which paragraph each formula stands in, by number. That is a sweep of the body — 5.1s to place the single formula of a real guide — where the anchor each one carries names the same place for two UNO calls",
                        "default": False
                    },
                    "document": {"type": "string", "description": DOCUMENT}
                }
            },
            "handler": self.list_formulas_live
        }

        self.tools["add_formula_live"] = {
            "description": "Put a formula in the text as an inline object, like one typed with Insert > Object > Formula. It goes at the start of the range the address names and takes nothing with it, unless replace_text is true, when the covered text is what the formula stands in for. An address at a caret — {\"paragraph\": N, \"offset\": K, \"length\": 0} — puts it exactly there",
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {
                        "type": "object",
                        "description": "Where: a paragraph, a range, a table cell, the selection, or an anchor",
                        "properties": ADDRESS_PARTS
                    },
                    "formula": {"type": "string", "description": STARMATH},
                    "name": {"type": "string",
                             "description": "What to call it. Omit and Writer chooses one (Object1, Object2, …), which list_formulas reports; a name already taken is refused"},
                    "replace_text": {
                        "type": "boolean",
                        "description": "Replace the text the address covers with the formula instead of putting it in front of that text",
                        "default": False
                    },
                    "track_changes": {
                        "type": "boolean",
                        "description": "Omit to follow the document's own setting; true records this edit, false refuses to record it"
                    },
                    "document": {"type": "string", "description": DOCUMENT}
                },
                "required": ["address", "formula"]
            },
            "handler": self.add_formula_live
        }

        self.tools["set_formula_live"] = {
            "description": "Change what a formula says, leaving it where it is. The edit is made inside the formula's own document and is **not** on the Undo list, so Ctrl+Z will not take it back: the result carries `was`, the text it had, and setting that again puts it back",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string",
                             "description": "Which formula, by the name list_formulas gives"},
                    "formula": {"type": "string", "description": STARMATH},
                    "document": {"type": "string", "description": DOCUMENT}
                },
                "required": ["name", "formula"]
            },
            "handler": self.set_formula_live
        }

        self.tools["delete_formula_live"] = {
            "description": "Take a formula away. The text around it stays, and the result says what the formula was and where, so add_formula can put it back",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string",
                             "description": "Which formula, by the name list_formulas gives"},
                    "document": {"type": "string", "description": DOCUMENT}
                },
                "required": ["name"]
            },
            "handler": self.delete_formula_live
        }

    def list_formulas_live(self, address: Any = None, number: bool = False,
                           document: Optional[str] = None) -> Dict[str, Any]:
        """The formulas of a document"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.list_formulas(address=address, number=number,
                                             doc=doc)

    def add_formula_live(self, address: Any, formula: str,
                         name: Optional[str] = None,
                         replace_text: bool = False,
                         track_changes: Optional[bool] = None,
                         document: Optional[str] = None) -> Dict[str, Any]:
        """Put a formula in the text"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.add_formula(address, formula, name=name,
                                           replace_text=replace_text,
                                           track_changes=track_changes,
                                           doc=doc)

    def set_formula_live(self, name: str, formula: str,
                         document: Optional[str] = None) -> Dict[str, Any]:
        """Change what a formula says"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.set_formula(name, formula, doc=doc)

    def delete_formula_live(self, name: str,
                            document: Optional[str] = None) -> Dict[str, Any]:
        """Take a formula away"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.delete_formula(name, doc=doc)
