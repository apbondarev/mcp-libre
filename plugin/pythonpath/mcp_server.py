"""
The MCP server the plugin runs, assembled from its parts.

This file holds what every tool shares — the registry, dispatch, and the
document a tool is asked to act on — while the tools themselves live beside
their subject, one module per part:

    mcp_reading_tools.py     where the reader is, what the document says
    mcp_text_tools.py        changing text, its language, its spelling
    mcp_formatting_tools.py  how text looks, and the styles behind it
    mcp_document_tools.py    documents: making, saving, closing, renaming
    mcp_image_tools.py       pictures, and a picture of a page
    mcp_comment_tools.py     the notes in the margin
    mcp_table_tools.py       tables

Adding a tool is still three edits, now in two files: a UNOBridge method in
its part of the bridge, a thin *_live handler here in its part of the server,
and the self.tools[...] entry beside it — the JSON Schema there is what
clients see, since execute_tool splats it as **parameters.
"""

import logging
from typing import Dict, Any, Optional, List
from uno_bridge import UNOBridge
from mcp_reading_tools import ReadingTools
from mcp_text_tools import TextTools
from mcp_formatting_tools import FormattingTools
from mcp_document_tools import DocumentTools
from mcp_image_tools import ImageTools
from mcp_comment_tools import CommentTools
from mcp_table_tools import TableTools

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class LibreOfficeMCPServer(TableTools, CommentTools, ImageTools, 
                           DocumentTools, FormattingTools, TextTools, 
                           ReadingTools):
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
        self._register_text()
        self._register_formatting()
        self._register_document()
        self._register_image()
        self._register_comment()
        self._register_table()

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
    
    
    
    
    
    def _target_document(self, document: Optional[str] = None) -> tuple:
        """(document, error) for a tool that may name a specific document"""
        doc = self.uno_bridge.document_for(document)
        if doc is None:
            if document:
                return None, {"success": False,
                              "error": f"No open document with URL {document}"}
            return None, {"success": False, "error": "No document available"}
        return doc, None
    
    
    
    
    
    















    








    
    
    


# Global instance
mcp_server = None

def get_mcp_server() -> LibreOfficeMCPServer:
    """Get or create the global MCP server instance"""
    global mcp_server
    if mcp_server is None:
        mcp_server = LibreOfficeMCPServer()
    return mcp_server
