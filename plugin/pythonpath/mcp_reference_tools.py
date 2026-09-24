"""The tools for captions and cross-references: the numbers and the pointers."""

from typing import Any, Dict, List, Optional

ADDRESS_SCOPE = ("Which part of the document: omit for all of it, "
                 "{\"heading\": N} for a section, {\"paragraph\": N} for one "
                 "paragraph, {\"paragraph\": N, \"through\": M} for a block, "
                 "a range, or {\"selection\": true}")

TARGET = ("What to point at, named in exactly one way: {\"heading\": N} for "
          "the heading at that paragraph — or {\"heading\": {\"anchor\": "
          "\"a7f3c1\"}}, which is what list_reference_targets hands out, "
          "since numbering a document's headings is a walk of it — and a "
          "bookmark is left on the heading either way, as Writer's own dialog "
          "does; {\"caption\": \"Figure 2\"} for a caption, {\"bookmark\": "
          "\"name\"} or {\"reference_mark\": \"name\"}. "
          "list_reference_targets reports every target with the `reference` "
          "object to pass straight back here")


class ReferenceTools:
    """Part of LibreOfficeMCPServer — see mcp_server.py."""

    def _register_reference(self):
        """The tools of this part, as clients see them."""
        self.tools["list_reference_targets_live"] = {
            "description": "List what a cross-reference can point at in a Writer document — headings, captions, bookmarks and reference marks — each with the text it covers, where it sits, and the `reference` object to hand to insert_cross_reference_live. Use it before writing a reference, since a reference to something that is not there shows \"Error: Reference source not found\" in the document",
            "parameters": {
                "type": "object",
                "properties": {
                    "kinds": {
                        "type": "array",
                        "items": {"type": "string",
                                  "enum": ["heading", "caption", "bookmark",
                                           "reference_mark"]},
                        "description": "Only these kinds of target; omit for all four"
                    },
                    "address": {
                        "type": "object",
                        "description": ADDRESS_SCOPE,
                        "properties": {
                            "heading": {"type": "integer"},
                            "anchor": {"type": ["string", "object"]},
                            "bookmark": {"type": "string"},
                            "paragraph": {"type": "integer"},
                            "through": {"type": "integer"},
                            "offset": {"type": "integer"},
                            "length": {"type": "integer"},
                            "selection": {"type": "boolean"}
                        }
                    },
                    "start": {
                        "type": "integer",
                        "description": "Which target of the answer to begin at, for paging through a document that holds many",
                        "default": 0
                    },
                    "count": {
                        "type": "integer",
                        "description": "How many targets to report. 200 when nobody says, and a default rather than a limit — a real guide holds 1689, and every heading reported is held by an anchor. `total` says how many there are and `more` whether another call is worth making",
                        "default": 200
                    },
                    "number": {
                        "type": "boolean",
                        "description": "Place every target by paragraph number, in reading order, and say which bookmark each heading already has. Those are sweeps of the body — the headings alone are 4.3s on a real guide, where searching for their styles is 0.4s — and naming a scope turns them on, since narrowing to a stretch means knowing where things are",
                        "default": False
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                }
            },
            "handler": self.list_reference_targets_live
        }

        self.tools["list_references_live"] = {
            "description": "List the cross-reference fields of a Writer document, each with what it points at, which part of the target it shows, the text it currently shows and where it sits. A reference whose target is gone is reported as broken — the document itself shows only \"Error: Reference source not found\", in the office's own language",
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {
                        "type": "object",
                        "description": ADDRESS_SCOPE,
                        "properties": {
                            "heading": {"type": "integer"},
                            "anchor": {"type": ["string", "object"]},
                            "bookmark": {"type": "string"},
                            "paragraph": {"type": "integer"},
                            "through": {"type": "integer"},
                            "offset": {"type": "integer"},
                            "length": {"type": "integer"},
                            "selection": {"type": "boolean"}
                        }
                    },
                    "number": {
                        "type": "boolean",
                        "description": "Work out each reference's paragraph number as well — a sweep of the body, 12s for the 926 references of a real guide, where the anchors cost two UNO calls apiece",
                        "default": False
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                }
            },
            "handler": self.list_references_live
        }

        self.tools["insert_caption_live"] = {
            "description": "Caption a picture, a table or a paragraph: a new paragraph holding the category, a number that counts itself, and the text. The number is a field, so a caption added in front of another renumbers everything after it and the references to them follow. Say what is being captioned in exactly one way — image, table or address",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "The caption itself, e.g. \"The schema of a query\". May be empty for a bare number"
                    },
                    "image": {
                        "type": "string",
                        "description": "Name of the picture to caption, from list_images_live"
                    },
                    "table": {
                        "type": "string",
                        "description": "Name of the table to caption, from list_tables_live"
                    },
                    "address": {
                        "type": "object",
                        "description": "The paragraph to caption: {\"paragraph\": N}, {\"anchor\": \"a7f3c1\"} or {\"selection\": true}",
                        "properties": {
                            "anchor": {"type": ["string", "object"]},
                            "bookmark": {"type": "string"},
                            "paragraph": {"type": "integer"},
                            "offset": {"type": "integer"},
                            "length": {"type": "integer"},
                            "selection": {"type": "boolean"}
                        }
                    },
                    "category": {
                        "type": "string",
                        "description": "The word before the number, which is also the sequence it counts in: \"Figure\", \"Table\", \"Illustration\", \"Drawing\", \"Text\", or a new one of your own — a document already carries the first five",
                        "default": "Figure"
                    },
                    "position": {
                        "type": "string",
                        "enum": ["below", "above"],
                        "description": "Which side of the thing the caption goes. A table that opens the document has no paragraph above it, and that is refused rather than written below",
                        "default": "below"
                    },
                    "numbering": {
                        "type": "string",
                        "enum": ["arabic", "roman_upper", "roman_lower",
                                 "letter_upper", "letter_lower"],
                        "description": "How the number is written",
                        "default": "arabic"
                    },
                    "separator": {
                        "type": "string",
                        "description": "What stands between the number and the text",
                        "default": ": "
                    },
                    "style": {
                        "type": "string",
                        "description": "Paragraph style for the caption; by default the style named after the category, or \"Caption\""
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
            "handler": self.insert_caption_live
        }

        self.tools["insert_cross_reference_live"] = {
            "description": "Insert a cross-reference where an address points: a field showing a heading's text, a caption's number, or what a bookmark or reference mark covers. The field follows its target, so \"see Figure 3\" stays right when a figure is added before it. The target is checked first, since a reference to something that is not there shows an error in the document instead",
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {
                        "type": "object",
                        "description": "Where the reference goes: {\"paragraph\": N, \"offset\": K, \"length\": 0} for a spot, {\"paragraph\": N, \"offset\": K, \"length\": L} to put it in place of that text, {\"anchor\": \"a7f3c1\"}, {\"table\": \"Table1\", \"cell\": \"A2\"} or {\"selection\": true}",
                        "properties": {
                            "anchor": {"type": ["string", "object"]},
                            "bookmark": {"type": "string"},
                            "paragraph": {"type": "integer"},
                            "offset": {"type": "integer"},
                            "length": {"type": "integer"},
                            "table": {"type": "string"},
                            "cell": {"type": "string"},
                            "selection": {"type": "boolean"}
                        }
                    },
                    "target": {
                        "type": "object",
                        "description": TARGET,
                        "properties": {
                            "heading": {"type": ["integer", "object"]},
                            "caption": {"type": "string"},
                            "bookmark": {"type": "string"},
                            "reference_mark": {"type": "string"}
                        }
                    },
                    "part": {
                        "type": "string",
                        "enum": ["text", "page", "above_below", "chapter",
                                 "category_and_number", "caption_text",
                                 "number", "number_no_context",
                                 "number_full_context"],
                        "description": "What the reference shows: \"text\" (the target's own words, the default for a heading, a bookmark or a mark), \"category_and_number\" (\"Figure 3\", the default for a caption), \"caption_text\", \"number\", \"page\", \"above_below\" (\"above\"/\"below\"), or the chapter number. A heading's number needs chapter numbering turned on, and shows nothing without it"
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
                "required": ["address", "target"]
            },
            "handler": self.insert_cross_reference_live
        }

    def list_reference_targets_live(self, kinds: Optional[List[str]] = None,
                                    address: Any = None, start: int = 0,
                                    count: Optional[int] = None,
                                    number: bool = False,
                                    document: Optional[str] = None
                                    ) -> Dict[str, Any]:
        """List what a cross-reference can point at"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.list_reference_targets(kinds=kinds,
                                                      address=address,
                                                      start=start, count=count,
                                                      number=number, doc=doc)

    def list_references_live(self, address: Any = None, number: bool = False,
                             document: Optional[str] = None) -> Dict[str, Any]:
        """List the cross-reference fields and say which are broken"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.list_references(address=address,
                                               number=number, doc=doc)

    def insert_caption_live(self, text: str, image: Optional[str] = None,
                            table: Optional[str] = None, address: Any = None,
                            category: str = "Figure", position: str = "below",
                            numbering: str = "arabic", separator: str = ": ",
                            style: Optional[str] = None,
                            track_changes: Optional[bool] = None,
                            document: Optional[str] = None) -> Dict[str, Any]:
        """Caption a picture, a table or a paragraph"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.insert_caption(
            text, address=address, table=table, image=image,
            category=category, position=position, numbering=numbering,
            separator=separator, style=style, track_changes=track_changes,
            doc=doc)

    def insert_cross_reference_live(self, address: Any, target: Any,
                                    part: Optional[str] = None,
                                    track_changes: Optional[bool] = None,
                                    document: Optional[str] = None
                                    ) -> Dict[str, Any]:
        """Insert a reference to a heading, caption, bookmark or mark"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.insert_cross_reference(
            address, target, part=part, track_changes=track_changes, doc=doc)
