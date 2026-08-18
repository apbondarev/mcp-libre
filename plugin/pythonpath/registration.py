import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import uno
import unohelper
import logging
import threading
import traceback

# File logging — works even if UNO swallows stderr
logging.basicConfig(
    level=logging.DEBUG,
    filename="/tmp/mcp_extension.log",
    filemode="a",
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)
logger.info("registration.py loaded")

try:
    from com.sun.star.frame import XDispatchProvider, XDispatch
    logger.info("XDispatchProvider/XDispatch imported OK")
except Exception as e:
    logger.error(f"interface import failed: {e}")

try:
    from com.sun.star.lang import XServiceInfo
    logger.info("XServiceInfo imported OK")
except Exception as e:
    logger.error(f"XServiceInfo import failed: {e}")

IMPLEMENTATION_NAME = "org.mcp.libreoffice.MCPExtension"
SERVICE_NAMES = ("com.sun.star.frame.ProtocolHandler",)

# Module-level server state shared across all MCPExtension instances
_server_started = False
_mcp_server = None
_ai_interface = None


class MCPDispatch(unohelper.Base, XDispatch):
    def __init__(self, action, extension):
        unohelper.Base.__init__(self)
        self.action = action
        self.extension = extension

    def dispatch(self, url, args):
        logger.info(f"dispatch called: action={self.action}")
        try:
            self.extension._execute_action(self.action)
        except Exception as e:
            logger.error(f"dispatch error: {e}\n{traceback.format_exc()}")

    def addStatusListener(self, listener, url):
        pass

    def removeStatusListener(self, listener, url):
        pass


class MCPExtension(unohelper.Base, XDispatchProvider, XServiceInfo):
    def __init__(self, ctx):
        unohelper.Base.__init__(self)
        self.ctx = ctx
        logger.info("MCPExtension created")

    def getTypes(self):
        result = unohelper.Base.getTypes(self)
        logger.info(f"getTypes called, returning {len(result)} types: {[str(t) for t in result]}")
        return result

    # XDispatchProvider
    def queryDispatch(self, url, target_frame, search_flags):
        logger.info(f"queryDispatch: Complete={url.Complete} Path={url.Path!r} Arguments={url.Arguments!r}")
        if url.Protocol == "org.mcp.libreoffice.extension:":
            action = url.Path or ""
            d = MCPDispatch(action, self)
            return d
        return None

    def queryDispatches(self, requests):
        return [self.queryDispatch(r.FeatureURL, r.FrameName, r.SearchFlags) for r in requests]

    # XServiceInfo
    def getImplementationName(self):
        return IMPLEMENTATION_NAME

    def supportsService(self, service_name):
        return service_name in SERVICE_NAMES

    def getSupportedServiceNames(self):
        return SERVICE_NAMES

    def _execute_action(self, action):
        logger.info(f"_execute_action: {action}")
        if action == "start_mcp_server":
            threading.Thread(target=self._start_mcp_server, daemon=True).start()
            self._show_dialog("MCP Server", "MCP Server is started\nhttp://localhost:8765")
        elif action == "stop_mcp_server":
            threading.Thread(target=self._stop_mcp_server, daemon=True).start()
            self._show_dialog("MCP Server", "MCP Server is stopped")
        elif action == "restart_mcp_server":
            threading.Thread(target=lambda: (self._stop_mcp_server(), self._start_mcp_server()), daemon=True).start()
            self._show_dialog("MCP Server", "MCP Server is restarted\nhttp://localhost:8765")
        elif action == "get_status":
            self._show_status()

    def _show_dialog(self, title, message):
        logger.info(f"{title}: {message}")
        try:
            smgr = self.ctx.ServiceManager
            desktop = smgr.createInstanceWithContext("com.sun.star.frame.Desktop", self.ctx)
            frame = desktop.getCurrentFrame()
            parent = frame.getContainerWindow() if frame else None
            toolkit = smgr.createInstanceWithContext("com.sun.star.awt.Toolkit", self.ctx)
            msgbox = toolkit.createMessageBox(
                parent,
                uno.Enum("com.sun.star.awt.MessageBoxType", "INFOBOX"),
                1,
                title,
                message,
            )
            msgbox.execute()
        except Exception as e:
            logger.error(f"dialog failed: {e}")

    def _show_status(self):
        status = "running on http://localhost:8765" if _server_started else "stopped"
        self._show_dialog("MCP Server Status", f"MCP Server is {status}")

    def _start_mcp_server(self):
        global _server_started, _mcp_server, _ai_interface
        try:
            if _server_started:
                logger.info("Already started")
                return
            from ai_interface import start_ai_interface
            from mcp_server import get_mcp_server
            _mcp_server = get_mcp_server()
            _ai_interface = start_ai_interface(port=8765, host="localhost")
            _server_started = True
            logger.info("MCP server started on http://localhost:8765")
        except Exception as e:
            logger.error(f"start failed: {e}\n{traceback.format_exc()}")

    def _stop_mcp_server(self):
        global _server_started, _mcp_server, _ai_interface
        try:
            if not _server_started:
                return
            if _ai_interface:
                from ai_interface import stop_ai_interface
                stop_ai_interface()
                _ai_interface = None
            _mcp_server = None
            _server_started = False
            logger.info("MCP server stopped")
        except Exception as e:
            logger.error(f"stop failed: {e}\n{traceback.format_exc()}")

def createInstance(ctx):
    logger.info("createInstance called")
    return MCPExtension(ctx)


def getSupportedServiceNames():
    return SERVICE_NAMES


def getImplementationName():
    return IMPLEMENTATION_NAME


g_ImplementationHelper = unohelper.ImplementationHelper()
g_ImplementationHelper.addImplementation(
    createInstance,
    IMPLEMENTATION_NAME,
    SERVICE_NAMES,
)
