"""
The MCP server the plugin runs, assembled from its parts.

This file holds what every tool shares — the registry, dispatch, and the
document a tool is asked to act on — while the tools themselves live beside
their subject, one module per part:

    mcp_reading_tools.py     where the reader is, what the document says
    mcp_anchor_tools.py      names for places that outlast an edit
    mcp_text_tools.py        changing text, its language, its spelling
    mcp_formatting_tools.py  how text looks, and the styles behind it
    mcp_document_tools.py    documents: making, saving, closing, renaming
    mcp_image_tools.py       pictures, and a picture of a page
    mcp_comment_tools.py     the notes in the margin
    mcp_field_tools.py       the bits that write themselves
    mcp_bookmark_tools.py    names the document keeps for places
    mcp_review_tools.py      the changes a document is keeping
    mcp_table_tools.py       tables
    mcp_table_shape_tools.py rows, columns, merged cells, sorting
    mcp_batch_tools.py       several calls as one edit

Adding a tool is still three edits, now in two files: a UNOBridge method in
its part of the bridge, a thin *_live handler here in its part of the server,
and the self.tools[...] entry beside it — the JSON Schema there is what
clients see, since execute_tool splats it as **parameters.
"""

import logging
import time
from typing import Dict, Any, Optional, List
from uno_bridge import UNOBridge
from mcp_reading_tools import ReadingTools
from mcp_anchor_tools import AnchorTools
from mcp_text_tools import TextTools
from mcp_formatting_tools import FormattingTools
from mcp_document_tools import DocumentTools
from mcp_image_tools import ImageTools
from mcp_comment_tools import CommentTools
from mcp_field_tools import FieldTools
from mcp_bookmark_tools import BookmarkTools
from mcp_reference_tools import ReferenceTools
from mcp_review_tools import ReviewTools
from mcp_table_tools import TableTools
from mcp_table_shape_tools import TableShapeTools
from mcp_batch_tools import BatchTools

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class LibreOfficeMCPServer(BatchTools, ReviewTools, FieldTools, BookmarkTools, ReferenceTools, TableShapeTools, TableTools, CommentTools, ImageTools, 
                           DocumentTools, FormattingTools, TextTools, 
                           AnchorTools, ReadingTools):
    """Embedded MCP server for LibreOffice plugin"""
    
    def __init__(self):
        """Initialize the MCP server"""
        self.uno_bridge = UNOBridge()
        self.tools = {}
        self._register_tools()
        logger.info("LibreOffice MCP Server initialized")
    
    def _register_tools(self):
        """Register all available MCP tools, part by part."""
        self._register_reading()
        self._register_anchor()
        self._register_text()
        self._register_formatting()
        self._register_document()
        self._register_image()
        self._register_comment()
        self._register_field()
        self._register_bookmark()
        self._register_reference()
        self._register_review()
        self._register_table()
        self._register_table_shape()
        self._register_batch()

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
                    "code": "NOT_FOUND",
                    "error": f"Unknown tool: {tool_name}",
                    "available_tools": list(self.tools.keys())
                }
            
            result = self._run_tool(tool_name, parameters)
            logger.info(f"Executed tool '{tool_name}' successfully")
            return result
            
        except Exception as e:
            logger.error(f"Error executing tool '{tool_name}': {e}")
            return {
                "success": False,
                "code": "FAILED",
                "error": str(e),
                "tool": tool_name,
                "parameters": parameters
            }
    
    def _run_tool(self, tool_name: str,
                  parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Call one tool's handler and answer with a dict, whatever happens.

        The dispatch execute_tool has always done, split out so batch_live
        can make the same call without going round the transport again.
        """
        started = time.monotonic()
        if tool_name not in self.tools:
            return self._timed({"success": False, "code": "NOT_FOUND",
                                "error": f"Unknown tool: {tool_name}",
                                "available_tools": list(self.tools.keys())},
                               started)
        try:
            outcome = self.tools[tool_name]["handler"](**(parameters or {}))
        except TypeError as e:
            logger.error(f"Tool '{tool_name}' was called wrongly: {e}")
            outcome = {"success": False, "code": "INVALID_PARAMETER",
                       "error": str(e), "tool": tool_name,
                       "parameters": parameters}
        except Exception as e:
            logger.error(f"Error executing tool '{tool_name}': {e}")
            outcome = {"success": False, "code": "FAILED", "error": str(e),
                       "tool": tool_name, "parameters": parameters}
        return self._timed(outcome, started)

    @staticmethod
    def _timed(outcome: Dict[str, Any], started: float) -> Dict[str, Any]:
        """Stamp a result with what it cost, and a refusal with a code.

        elapsed_ms is here rather than in each tool because every call goes
        through this one place — and because a slow tool was a thing only a
        human with a stopwatch could see: find_text took ten seconds for
        twenty hits for weeks, and nothing in its answer said so. A refusal
        that named no code gets FAILED, so a caller can always branch on one
        rather than on English.
        """
        if not isinstance(outcome, dict):
            return outcome
        outcome["elapsed_ms"] = int((time.monotonic() - started) * 1000)
        if outcome.get("success") is False and "code" not in outcome:
            outcome["code"] = "FAILED"
        return outcome

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
    
    
    
    
    
    def _target_document(self, document: Optional[str] = None) -> tuple:
        """(document, error) for a tool that may name a specific document"""
        doc = self.uno_bridge.document_for(document)
        if doc is None:
            if document:
                return None, {"success": False, "code": "NOT_FOUND",
                              "error": f"No open document with URL {document}"}
            return None, {"success": False, "code": "NO_DOCUMENT", "error": "No document available"}
        return doc, None
    
    
    
    
    
    















    








    
    
    


# Global instance
mcp_server = None

def get_mcp_server() -> LibreOfficeMCPServer:
    """Get or create the global MCP server instance"""
    global mcp_server
    if mcp_server is None:
        mcp_server = LibreOfficeMCPServer()
    return mcp_server
