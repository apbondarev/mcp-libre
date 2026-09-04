"""
LibreOffice MCP Extension - MCP Server Module

This module implements an embedded MCP server that integrates with LibreOffice
via the UNO API, providing real-time document manipulation capabilities.
"""

import asyncio
import json
import logging
from typing import Dict, Any, Optional, List
from uno_bridge import UNOBridge

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class LibreOfficeMCPServer:
    """Embedded MCP server for LibreOffice plugin"""
    
    def __init__(self):
        """Initialize the MCP server"""
        self.uno_bridge = UNOBridge()
        self.tools = {}
        self._register_tools()
        logger.info("LibreOffice MCP Server initialized")
    
    def _register_tools(self):
        """Register all available MCP tools"""
        
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
        
        # Text manipulation tools
        self.tools["insert_text_live"] = {
            "description": "Insert text into the currently active document. This inserts and never replaces: with text selected it inserts at the start of the selection and leaves the original in place. To replace text use replace_selection_live or replace_range_live",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Text to insert"
                    },
                    "position": {
                        "type": "integer",
                        "description": "Position to insert at (optional, defaults to cursor position)"
                    }
                },
                "required": ["text"]
            },
            "handler": self.insert_text_live
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
        
        # Text formatting tools
        self.tools["format_text_live"] = {
            "description": "Apply formatting to selected text in active document",
            "parameters": {
                "type": "object",
                "properties": {
                    "bold": {
                        "type": "boolean",
                        "description": "Apply bold formatting"
                    },
                    "italic": {
                        "type": "boolean",
                        "description": "Apply italic formatting"
                    },
                    "underline": {
                        "type": "boolean",
                        "description": "Apply underline formatting"
                    },
                    "font_size": {
                        "type": "number",
                        "description": "Font size in points"
                    },
                    "font_name": {
                        "type": "string",
                        "description": "Font family name"
                    }
                }
            },
            "handler": self.format_text_live
        }
        
        # Cursor and selection tools
        self.tools["get_cursor_info_live"] = {
            "description": "Get the cursor position, the paragraph containing the cursor, and the selected text in the active Writer document",
            "parameters": {
                "type": "object",
                "properties": {}
            },
            "handler": self.get_cursor_info_live
        }
        
        # Reading tools
        self.tools["read_paragraphs_live"] = {
            "description": "Read a window of paragraphs from the active Writer document, with their indices and styles",
            "parameters": {
                "type": "object",
                "properties": {
                    "start": {
                        "type": "integer",
                        "description": "Index of the first paragraph to read (0-based)",
                        "default": 0
                    },
                    "count": {
                        "type": "integer",
                        "description": "How many paragraphs to read (max 200)",
                        "default": 50
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                }
            },
            "handler": self.read_paragraphs_live
        }
        
        self.tools["get_outline_live"] = {
            "description": "List the headings of the active Writer document with the paragraph index of each",
            "parameters": {
                "type": "object",
                "properties": {
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                }
            },
            "handler": self.get_outline_live
        }
        
        self.tools["find_text_live"] = {
            "description": "Find text in the active Writer document, returning an address for each match that other tools can act on",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Text or regular expression to search for"
                    },
                    "regex": {
                        "type": "boolean",
                        "description": "Treat the query as a regular expression",
                        "default": False
                    },
                    "case_sensitive": {
                        "type": "boolean",
                        "description": "Match case exactly",
                        "default": False
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "How many matches to return (max 200)",
                        "default": 50
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                },
                "required": ["query"]
            },
            "handler": self.find_text_live
        }
        
        # Editing tools
        self.tools["replace_selection_live"] = {
            "description": "Replace the text currently selected in the active Writer document. Fails when nothing is selected",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Text to put in place of the selection"
                    },
                    "track_changes": {
                        "type": "boolean",
                        "description": "Omit to follow the document's own setting (see track_changes in get_document_info_live). True records this edit as a change to accept or reject, which leaves the original in place struck through. False refuses to record it even in a document that records everything. Either way the document's setting is left as its owner had it, and the result reports which happened"
                    },
                    "language": {
                        "type": "string",
                        "description": "Language tag for the new text, such as \"ru-RU\". ALWAYS set this when writing text in a different language from what it replaces — translating, for instance. Without it the new text keeps the locale of the text it replaced, Writer spell-checks it against the wrong dictionary, and every single word appears underlined in red even though it is spelled correctly"
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                },
                "required": ["text"]
            },
            "handler": self.replace_selection_live
        }
        
        self.tools["replace_range_live"] = {
            "description": "Replace the text at an address in the active Writer document. Use the addresses returned by get_outline_live, read_paragraphs_live and find_text_live — this is how text is rewritten without a human selecting it first",
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {
                        "type": "object",
                        "description": "Where to replace: {\"paragraph\": N} for a whole body paragraph, {\"paragraph\": N, \"offset\": K, \"length\": L} for part of one, or {\"selection\": true} for the current selection",
                        "properties": {
                            "paragraph": {"type": "integer", "description": "0-based body paragraph index"},
                            "offset": {"type": "integer", "description": "Characters from the paragraph start, default 0"},
                            "length": {"type": "integer", "description": "Characters to replace, default to the end of the paragraph"},
                            "selection": {"type": "boolean", "description": "Use the current selection instead of an index"}
                        }
                    },
                    "text": {
                        "type": "string",
                        "description": "Text to put in place of what the address points at"
                    },
                    "track_changes": {
                        "type": "boolean",
                        "description": "Omit to follow the document's own setting (see track_changes in get_document_info_live). True records this edit as a change to accept or reject, which leaves the original in place struck through. False refuses to record it even in a document that records everything. Either way the document's setting is left as its owner had it, and the result reports which happened"
                    },
                    "language": {
                        "type": "string",
                        "description": "Language tag for the new text, such as \"ru-RU\". ALWAYS set this when writing text in a different language from what it replaces — translating, for instance. Without it the new text keeps the locale of the text it replaced, Writer spell-checks it against the wrong dictionary, and every single word appears underlined in red even though it is spelled correctly"
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                },
                "required": ["address", "text"]
            },
            "handler": self.replace_range_live
        }
        
        self.tools["set_language_live"] = {
            "description": "Mark the text at an address as being in a language, so Writer spell-checks it against the right dictionary. Text left with the wrong language is underlined word by word even when it is spelled correctly",
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {
                        "type": "object",
                        "description": "Where to set the language: {\"paragraph\": N}, {\"paragraph\": N, \"offset\": K, \"length\": L} or {\"selection\": true}",
                        "properties": {
                            "paragraph": {"type": "integer"},
                            "offset": {"type": "integer"},
                            "length": {"type": "integer"},
                            "selection": {"type": "boolean"}
                        }
                    },
                    "language": {
                        "type": "string",
                        "description": "Language tag such as \"ru-RU\", \"en-US\" or just \"de\""
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                },
                "required": ["address", "language"]
            },
            "handler": self.set_language_live
        }
        
        self.tools["check_spelling_live"] = {
            "description": "Report misspelled words in the active Writer document, each with the address of the word and the dictionary's suggestions. Every word is judged against the language of the text it sits in, so check the reported language before trusting a hit: text marked with the wrong language is reported as misspelled even when it is correct, and set_language_live fixes that",
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {
                        "type": "object",
                        "description": "Limit the check to one paragraph, e.g. {\"paragraph\": 4}. Omit to check the whole document",
                        "properties": {
                            "paragraph": {"type": "integer"},
                            "offset": {"type": "integer"},
                            "length": {"type": "integer"},
                            "selection": {"type": "boolean"}
                        }
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "How many misspellings to report (max 200)",
                        "default": 50
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                }
            },
            "handler": self.check_spelling_live
        }
        
        # Formatting tools
        self.tools["format_range_live"] = {
            "description": "Apply character formatting to the text at an address in the active Writer document. Unlike format_text_live this needs no selection, so an assistant can format a paragraph it found with get_outline_live or find_text_live",
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {
                        "type": "object",
                        "description": "Where to act: {\"paragraph\": N} for a whole body paragraph, {\"paragraph\": N, \"offset\": K, \"length\": L} for part of one, or {\"selection\": true}",
                        "properties": {
                            "paragraph": {"type": "integer"},
                            "offset": {"type": "integer"},
                            "length": {"type": "integer"},
                            "selection": {"type": "boolean"}
                        }
                    },
                    "bold": {"type": "boolean", "description": "Bold on or off"},
                    "italic": {"type": "boolean", "description": "Italic on or off"},
                    "underline": {"type": "boolean", "description": "Underline on or off"},
                    "font_size": {"type": "number", "description": "Font size in points"},
                    "font_name": {"type": "string", "description": "Font family, e.g. \"Liberation Mono\" for a code block"},
                    "color": {
                        "type": "string",
                        "description": "Text colour as #RRGGBB, e.g. \"#0000CC\". This is what syntax highlighting is made of: find the tokens with find_text_live and colour each one"
                    },
                    "background_color": {
                        "type": "string",
                        "description": "Colour behind the characters as #RRGGBB, e.g. \"#FFFFCC\" to highlight a phrase"
                    },
                    "track_changes": {
                        "type": "boolean",
                        "description": "Omit to follow the document's own setting; true records this change, false refuses to record it"
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                },
                "required": ["address"]
            },
            "handler": self.format_range_live
        }
        
        self.tools["apply_paragraph_style_live"] = {
            "description": "Give the paragraphs at an address a paragraph style. Prefer this over setting a font by hand for code blocks and quotations: \"Preformatted Text\" carries the monospace font and the spacing together. Use list_styles_live for the names this document has",
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {
                        "type": "object",
                        "description": "Where to act: {\"paragraph\": N} for a whole body paragraph, {\"paragraph\": N, \"offset\": K, \"length\": L} for part of one, or {\"selection\": true}",
                        "properties": {
                            "paragraph": {"type": "integer"},
                            "offset": {"type": "integer"},
                            "length": {"type": "integer"},
                            "selection": {"type": "boolean"}
                        }
                    },
                    "style": {
                        "type": "string",
                        "description": "Paragraph style name, e.g. \"Preformatted Text\", \"Heading 2\", \"Quotations\""
                    },
                    "track_changes": {
                        "type": "boolean",
                        "description": "Omit to follow the document's own setting; true records this change, false refuses to record it"
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                },
                "required": ["address", "style"]
            },
            "handler": self.apply_paragraph_style_live
        }
        
        self.tools["format_paragraph_live"] = {
            "description": "Put a border and a background behind the paragraph at an address. Formatting several consecutive paragraphs the same way draws one box around the group, which is how a code block gets framed",
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {
                        "type": "object",
                        "description": "Which paragraph: {\"paragraph\": N} or {\"selection\": true}",
                        "properties": {
                            "paragraph": {"type": "integer"},
                            "offset": {"type": "integer"},
                            "length": {"type": "integer"},
                            "selection": {"type": "boolean"}
                        }
                    },
                    "background_color": {
                        "type": "string",
                        "description": "Fill behind the paragraph as #RRGGBB, e.g. \"#F5F5F5\" for a code block"
                    },
                    "border": {
                        "type": "boolean",
                        "description": "True draws a box on all four sides, false removes it"
                    },
                    "border_color": {
                        "type": "string",
                        "description": "Border colour as #RRGGBB, default \"#808080\"",
                        "default": "#808080"
                    },
                    "border_width": {
                        "type": "number",
                        "description": "Border thickness in points, default 0.5",
                        "default": 0.5
                    },
                    "padding": {
                        "type": "number",
                        "description": "Space between the border and the text, in points"
                    },
                    "track_changes": {
                        "type": "boolean",
                        "description": "Omit to follow the document's own setting; true records this change, false refuses to record it"
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                },
                "required": ["address"]
            },
            "handler": self.format_paragraph_live
        }
        
        self.tools["list_styles_live"] = {
            "description": "List the style names the active Writer document has, so a style is never guessed at",
            "parameters": {
                "type": "object",
                "properties": {
                    "family": {
                        "type": "string",
                        "description": "Which family to list: ParagraphStyles (default), CharacterStyles, PageStyles, FrameStyles, NumberingStyles",
                        "default": "ParagraphStyles"
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                }
            },
            "handler": self.list_styles_live
        }
        
        # Document saving tools
        self.tools["save_document_live"] = {
            "description": "Save the currently active document",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to save document to (optional, saves to current location if not specified)"
                    }
                }
            },
            "handler": self.save_document_live
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
        
        # Content reading tools
        self.tools["get_text_content_live"] = {
            "description": "Get the text content of the currently active document",
            "parameters": {
                "type": "object",
                "properties": {}
            },
            "handler": self.get_text_content_live
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
        
        logger.info(f"Registered {len(self.tools)} MCP tools")
    
    async def execute_tool(self, tool_name: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute an MCP tool
        
        Args:
            tool_name: Name of the tool to execute
            parameters: Parameters for the tool
            
        Returns:
            Result dictionary
        """
        try:
            if tool_name not in self.tools:
                return {
                    "success": False,
                    "error": f"Unknown tool: {tool_name}",
                    "available_tools": list(self.tools.keys())
                }
            
            tool = self.tools[tool_name]
            handler = tool["handler"]
            
            # Execute the tool handler
            result = handler(**parameters)
            
            logger.info(f"Executed tool '{tool_name}' successfully")
            return result
            
        except Exception as e:
            logger.error(f"Error executing tool '{tool_name}': {e}")
            return {
                "success": False,
                "error": str(e),
                "tool": tool_name,
                "parameters": parameters
            }
    
    def get_tool_list(self) -> List[Dict[str, Any]]:
        """Get list of available tools with their descriptions"""
        return [
            {
                "name": name,
                "description": tool["description"],
                "parameters": tool["parameters"]
            }
            for name, tool in self.tools.items()
        ]
    
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
    
    def insert_text_live(self, text: str, position: Optional[int] = None) -> Dict[str, Any]:
        """Insert text into the currently active document"""
        return self.uno_bridge.insert_text(text, position)
    
    def get_document_info_live(self) -> Dict[str, Any]:
        """Get information about the currently active document"""
        doc_info = self.uno_bridge.get_document_info()
        if "error" in doc_info:
            return {"success": False, **doc_info}
        else:
            return {"success": True, "document_info": doc_info}
    
    def format_text_live(self, **formatting) -> Dict[str, Any]:
        """Apply formatting to selected text"""
        return self.uno_bridge.format_text(formatting)
    
    def _target_document(self, document: Optional[str] = None) -> tuple:
        """(document, error) for a tool that may name a specific document"""
        doc = self.uno_bridge.document_for(document)
        if doc is None:
            if document:
                return None, {"success": False,
                              "error": f"No open document with URL {document}"}
            return None, {"success": False, "error": "No document available"}
        return doc, None
    
    def get_cursor_info_live(self) -> Dict[str, Any]:
        """Get cursor position, current paragraph and selected text"""
        return self.uno_bridge.get_cursor_info()
    
    def read_paragraphs_live(self, start: int = 0, count: int = 50,
                             document: Optional[str] = None) -> Dict[str, Any]:
        """Read a window of paragraphs from a Writer document"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.read_paragraphs(start=start, count=count, doc=doc)
    
    def get_outline_live(self, document: Optional[str] = None) -> Dict[str, Any]:
        """List the headings of a Writer document"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.get_outline(doc=doc)
    
    def find_text_live(self, query: str, regex: bool = False,
                       case_sensitive: bool = False, max_results: int = 50,
                       document: Optional[str] = None) -> Dict[str, Any]:
        """Find text in a Writer document"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.find_text(query, regex=regex,
                                         case_sensitive=case_sensitive,
                                         max_results=max_results, doc=doc)
    
    def replace_selection_live(self, text: str,
                               track_changes: Optional[bool] = None,
                               language: Optional[str] = None,
                               document: Optional[str] = None) -> Dict[str, Any]:
        """Replace the selected text in a Writer document"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.replace_selection(text, track_changes=track_changes,
                                                 language=language, doc=doc)
    
    def replace_range_live(self, address: Any, text: str,
                           track_changes: Optional[bool] = None,
                           language: Optional[str] = None,
                           document: Optional[str] = None) -> Dict[str, Any]:
        """Replace the text at an address in a Writer document"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.replace_range(address, text,
                                             track_changes=track_changes,
                                             language=language, doc=doc)

    def check_spelling_live(self, address: Any = None, max_results: int = 50,
                            document: Optional[str] = None) -> Dict[str, Any]:
        """Report misspelled words with an address and suggestions for each"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.check_spelling(address=address,
                                              max_results=max_results, doc=doc)

    def set_language_live(self, address: Any, language: str,
                          document: Optional[str] = None) -> Dict[str, Any]:
        """Mark the text at an address as being in a language"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.set_language(address, language, doc=doc)
    
    def format_range_live(self, address: Any, bold: Optional[bool] = None,
                          italic: Optional[bool] = None,
                          underline: Optional[bool] = None,
                          font_size: Optional[float] = None,
                          font_name: Optional[str] = None,
                          color: Any = None,
                          background_color: Any = None,
                          track_changes: Optional[bool] = None,
                          document: Optional[str] = None) -> Dict[str, Any]:
        """Apply character formatting to the text at an address"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.format_range(address, bold=bold, italic=italic,
                                            underline=underline,
                                            font_size=font_size,
                                            font_name=font_name, color=color,
                                            background_color=background_color,
                                            track_changes=track_changes, doc=doc)

    def format_paragraph_live(self, address: Any, background_color: Any = None,
                              border: Optional[bool] = None,
                              border_color: Any = "#808080",
                              border_width: float = 0.5,
                              padding: Optional[float] = None,
                              track_changes: Optional[bool] = None,
                              document: Optional[str] = None) -> Dict[str, Any]:
        """Put a border and a background behind a paragraph"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.format_paragraph(
            address, background_color=background_color, border=border,
            border_color=border_color, border_width=border_width,
            padding=padding, track_changes=track_changes, doc=doc)

    def apply_paragraph_style_live(self, address: Any, style: str,
                                   track_changes: Optional[bool] = None,
                                   document: Optional[str] = None) -> Dict[str, Any]:
        """Give the paragraphs at an address a paragraph style"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.apply_paragraph_style(
            address, style, track_changes=track_changes, doc=doc)

    def list_styles_live(self, family: str = "ParagraphStyles",
                         document: Optional[str] = None) -> Dict[str, Any]:
        """List the style names the document has"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.list_styles(family=family, doc=doc)

    def save_document_live(self, file_path: Optional[str] = None) -> Dict[str, Any]:
        """Save the currently active document"""
        return self.uno_bridge.save_document(file_path=file_path)
    
    def export_document_live(self, export_format: str, file_path: str) -> Dict[str, Any]:
        """Export the currently active document"""
        return self.uno_bridge.export_document(export_format, file_path)
    
    def get_text_content_live(self) -> Dict[str, Any]:
        """Get text content of the currently active document"""
        return self.uno_bridge.get_text_content()
    
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


# Global instance
mcp_server = None

def get_mcp_server() -> LibreOfficeMCPServer:
    """Get or create the global MCP server instance"""
    global mcp_server
    if mcp_server is None:
        mcp_server = LibreOfficeMCPServer()
    return mcp_server
