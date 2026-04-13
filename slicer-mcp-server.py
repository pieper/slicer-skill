"""
Slicer MCP Server — paste this into the Slicer Python console.

Implements an MCP (Model Context Protocol) server as a request handler
for Slicer's built-in WebServer module.  Uses the Streamable HTTP transport
in JSON-response mode (no SSE), so zero external dependencies are needed.

Usage:
  1. Paste this entire script into the Slicer Python interactor
  2. Configure your MCP client:

     Claude Desktop / Claude Code / Cline — claude_desktop_config.json:
       {
         "mcpServers": {
           "slicer": { "url": "http://localhost:2026/mcp" }
         }
       }

  3. The MCP client can now list_nodes, execute_python, take screenshots, etc.

To stop:  mcpLogic.stop()

Protocol reference:  https://modelcontextprotocol.io/specification/2025-03-26
"""

import json
import base64
import traceback
import urllib

import qt
import vtk
import slicer

import WebServer
import WebServerLib


# ─── Logging ─────────────────────────────────────────────────

import tempfile
import os
import datetime

_mcpLogFile = os.path.join(tempfile.gettempdir(), f"slicer-mcp-{os.getpid()}.log")

def mcpFileLog(*args):
    """Append a timestamped line to the MCP log file (used by WebServerLogic)."""
    line = " ".join(str(a) for a in args)
    timestamp = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
    with open(_mcpLogFile, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {line}\n")

def mcpStatusMessage(msg):
    """Show an MCP activity message in Slicer's status bar (auto-clears after 5 s)."""
    slicer.util.mainWindow().statusBar().showMessage(str(msg), 5000)


# ─── Protocol constants ──────────────────────────────────────

PROTOCOL_VERSION = "2025-03-26"
SERVER_INFO = {"name": "slicer-mcp", "version": "0.1.0"}
SERVER_CAPABILITIES = {"tools": {}}


# ─── Tool registry ───────────────────────────────────────────
# Each tool: schema dict  +  handler function(args) -> content list

MCP_TOOLS = []
TOOL_HANDLERS = {}

def mcp_tool(name, description, inputSchema):
    """Decorator to register a function as an MCP tool."""
    def decorator(func):
        MCP_TOOLS.append({
            "name": name,
            "description": description,
            "inputSchema": inputSchema,
        })
        TOOL_HANDLERS[name] = func
        return func
    return decorator


@mcp_tool(
    name="list_nodes",
    description=(
        "List MRML nodes in the Slicer scene. "
        "Returns name, id, and class for each node."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "className": {
                "type": "string",
                "description": (
                    "MRML node class filter "
                    "(e.g. 'vtkMRMLScalarVolumeNode'). "
                    "Default: all nodes."
                ),
            }
        },
    },
)
def tool_list_nodes(args):
    className = args.get("className", "vtkMRMLNode")
    nodes = slicer.util.getNodesByClass(className)
    result = [
        {"name": n.GetName(), "id": n.GetID(), "class": n.GetClassName()}
        for n in nodes
    ]
    return [{"type": "text", "text": json.dumps(result, indent=2)}]


@mcp_tool(
    name="get_node_properties",
    description="Get the full property dump of an MRML node by its ID.",
    inputSchema={
        "type": "object",
        "properties": {
            "id": {
                "type": "string",
                "description": "MRML node ID (e.g. 'vtkMRMLScalarVolumeNode1')",
            }
        },
        "required": ["id"],
    },
)
def tool_get_node_properties(args):
    node = slicer.mrmlScene.GetNodeByID(args["id"])
    if not node:
        return [{"type": "text", "text": f"Node '{args['id']}' not found"}]
    return [{"type": "text", "text": str(node)}]


@mcp_tool(
    name="execute_python",
    description=(
        "Execute Python code in the running Slicer environment. "
        "Set the variable __result to return a value to the caller."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "Python code to execute"}
        },
        "required": ["code"],
    },
)
def tool_execute_python(args):
    code = args["code"]
    exec_globals = {"__builtins__": __builtins__, "__result": None}
    # Make common Slicer namespaces available
    for name in ("slicer", "vtk", "qt", "ctk"):
        module = globals().get(name)
        if module:
            exec_globals[name] = module
    try:
        exec(code, exec_globals)
    except Exception:
        return [{"type": "text", "text": f"Error:\n{traceback.format_exc()}"}]
    result = exec_globals.get("__result")
    if result is not None:
        return [{"type": "text", "text": str(result)}]
    return [{"type": "text", "text": "OK (no __result set)"}]


@mcp_tool(
    name="screenshot",
    description="Capture the Slicer application window as a PNG image.",
    inputSchema={"type": "object", "properties": {}},
)
def tool_screenshot(_args):
    slicer.app.processEvents()
    slicer.util.forceRenderAllViews()
    pixmap = slicer.util.mainWindow().grab()
    byteArray = qt.QByteArray()
    buf = qt.QBuffer(byteArray)
    buf.open(qt.QIODevice.WriteOnly)
    pixmap.save(buf, "PNG")
    b64 = base64.b64encode(bytes(byteArray.data())).decode("ascii")
    return [{"type": "image", "data": b64, "mimeType": "image/png"}]


@mcp_tool(
    name="write_file",
    description=(
        "Write content to a file on the Slicer host filesystem. "
        "For text files use utf-8 encoding (default). "
        "For binary files, base64-encode the content and set encoding to 'base64'."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute file path to write",
            },
            "content": {
                "type": "string",
                "description": "File content (text or base64-encoded binary)",
            },
            "encoding": {
                "type": "string",
                "enum": ["utf-8", "base64"],
                "description": "Content encoding (default: utf-8)",
            },
        },
        "required": ["path", "content"],
    },
)
def tool_write_file(args):
    path = args["path"]
    content = args["content"]
    encoding = args.get("encoding", "utf-8")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if encoding == "base64":
        data = base64.b64decode(content)
        with open(path, "wb") as f:
            f.write(data)
    else:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    return [{"type": "text", "text": f"Wrote {os.path.getsize(path)} bytes to {path}"}]


@mcp_tool(
    name="read_file",
    description=(
        "Read a file from the Slicer host filesystem. "
        "Returns text content for text files. "
        "Set encoding to 'base64' to read binary files."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute file path to read",
            },
            "encoding": {
                "type": "string",
                "enum": ["utf-8", "base64"],
                "description": "Content encoding (default: utf-8)",
            },
        },
        "required": ["path"],
    },
)
def tool_read_file(args):
    path = args["path"]
    encoding = args.get("encoding", "utf-8")
    if not os.path.exists(path):
        return [{"type": "text", "text": f"File not found: {path}"}]
    if encoding == "base64":
        with open(path, "rb") as f:
            data = base64.b64encode(f.read()).decode("ascii")
        return [{"type": "text", "text": data}]
    else:
        with open(path, "r", encoding="utf-8") as f:
            return [{"type": "text", "text": f.read()}]


@mcp_tool(
    name="load_sample_data",
    description=(
        "Load a named sample dataset into Slicer. "
        "Common names: MRHead, CTChest, CTACardio, MRBrainTumor1."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Sample dataset name"}
        },
        "required": ["name"],
    },
)
def tool_load_sample_data(args):
    import SampleData
    name = args["name"]
    try:
        SampleData.downloadSample(name)
    except Exception as e:
        return [{"type": "text", "text": f"Error loading '{name}': {e}"}]
    return [{"type": "text", "text": f"Sample data '{name}' loaded."}]


# ─── JSON-RPC helpers ────────────────────────────────────────

def _ok(id, result):
    return {"jsonrpc": "2.0", "id": id, "result": result}

def _err(id, code, message):
    return {"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": message}}


def _dispatch(msg):
    """Route a single JSON-RPC message and return a response dict, or None for notifications."""
    method = msg.get("method")
    msg_id = msg.get("id")      # None for notifications
    params = msg.get("params", {})

    # ── Lifecycle ────────────────────
    if method == "initialize":
        return _ok(msg_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": SERVER_CAPABILITIES,
            "serverInfo": SERVER_INFO,
        })

    if method == "notifications/initialized":
        return None  # notification — no JSON-RPC response

    if method == "notifications/cancelled":
        return None

    # ── Ping ─────────────────────────
    if method == "ping":
        return _ok(msg_id, {})

    # ── Tools ────────────────────────
    if method == "tools/list":
        return _ok(msg_id, {"tools": MCP_TOOLS})

    if method == "tools/call":
        tool_name = params.get("name")
        arguments = params.get("arguments", {})
        handler = TOOL_HANDLERS.get(tool_name)
        if not handler:
            return _err(msg_id, -32602, f"Unknown tool: {tool_name}")
        try:
            content = handler(arguments)
            return _ok(msg_id, {"content": content, "isError": False})
        except Exception:
            return _ok(msg_id, {
                "content": [{"type": "text", "text": traceback.format_exc()}],
                "isError": True,
            })

    # ── Unknown ──────────────────────
    if msg_id is not None:
        return _err(msg_id, -32601, f"Method not found: {method}")
    return None  # unknown notification — ignore silently


# ─── WebServer request handler ───────────────────────────────

class MCPRequestHandler(WebServerLib.BaseRequestHandler):
    """
    Handles MCP Streamable HTTP transport at the /mcp endpoint.

    Accepts POST with JSON-RPC messages and returns application/json.
    GET (for SSE streams) is not implemented — returns an error.

    See: https://modelcontextprotocol.io/specification/2025-03-26/basic/transports#streamable-http
    """

    # Shared across instances — once the user decides, it sticks for the session
    _access_allowed = None  # None = not yet asked, True/False = user's choice

    def __init__(self, logMessage=None):
        self.logMessage = logMessage or mcpFileLog

    def _check_access(self) -> bool:
        """Show a confirmation dialog on first access.  Returns True if allowed."""
        if MCPRequestHandler._access_allowed is not None:
            return MCPRequestHandler._access_allowed

        msgBox = qt.QMessageBox(slicer.util.mainWindow())
        msgBox.setWindowTitle("MCP Client Requesting Connection")
        msgBox.setIcon(qt.QMessageBox.Warning)
        msgBox.setText("Allow MCP client access?")
        msgBox.setInformativeText(
            "If you allow this, the client will be able to execute "
            "arbitrary Python code in this Slicer session with full "
            "access to the filesystem, network, and any loaded data "
            "(including patient data).\n\n"
            "Only allow this if you trust the connecting application."
        )
        msgBox.setStandardButtons(qt.QMessageBox.Yes | qt.QMessageBox.No)
        msgBox.setDefaultButton(qt.QMessageBox.No)
        MCPRequestHandler._access_allowed = msgBox.exec() == qt.QMessageBox.Yes
        if MCPRequestHandler._access_allowed:
            mcpFileLog("MCP access ALLOWED by user")
            mcpStatusMessage("MCP: client connected")
        else:
            mcpFileLog("MCP access DENIED by user")
            mcpStatusMessage("MCP: client connection denied")
        return MCPRequestHandler._access_allowed

    def _handleFileRequest(self, method, parsedURL, requestBody):
        """
        Raw HTTP file transfer at /file?path=<absolute-path>.

        POST: write requestBody to file  (curl --data-binary @local.py URL)
        GET:  read file and return contents
        """
        qs = urllib.parse.parse_qs(parsedURL.query)
        paths = qs.get(b"path") or qs.get("path")
        if not paths:
            body = {"error": "Missing required query parameter: path"}
            return b"application/json", json.dumps(body).encode()
        dest = paths[0].decode() if isinstance(paths[0], bytes) else paths[0]

        if method == "POST":
            try:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, "wb") as f:
                    written = f.write(requestBody)
                mcpFileLog(f"FILE write {written} bytes -> {dest}")
                mcpStatusMessage(f"MCP: wrote {dest}")
                body = {"wrote": written, "path": dest}
            except Exception:
                body = {"error": traceback.format_exc()}
            return b"application/json", json.dumps(body).encode()

        if method == "GET":
            try:
                with open(dest, "rb") as f:
                    data = f.read()
                mcpFileLog(f"FILE read {len(data)} bytes <- {dest}")
                return b"application/octet-stream", data
            except FileNotFoundError:
                body = {"error": f"File not found: {dest}"}
                return b"application/json", json.dumps(body).encode()
            except Exception:
                body = {"error": traceback.format_exc()}
                return b"application/json", json.dumps(body).encode()

        body = {"error": f"Unsupported method for /file: {method}"}
        return b"application/json", json.dumps(body).encode()

    def canHandleRequest(self, uri: bytes, **_kwargs) -> float:
        parsedURL = urllib.parse.urlparse(uri)
        if parsedURL.path in (b"/mcp", b"/file"):
            return 0.5
        return 0.0

    def handleRequest(
        self, method: str, uri: bytes, requestBody: bytes, **_kwargs
    ) -> tuple[bytes, bytes]:

        if not self._check_access():
            body = _err(None, -32600, "Access denied by user")
            return b"application/json", json.dumps(body).encode()

        # ── /file endpoint — raw HTTP file transfer ──
        parsedURL = urllib.parse.urlparse(uri)
        if parsedURL.path == b"/file":
            return self._handleFileRequest(method, parsedURL, requestBody)

        if method == "GET":
            # SSE stream endpoint — not implemented
            body = _err(None, -32600, "SSE streaming not implemented; use POST")
            return b"application/json", json.dumps(body).encode()

        if method == "DELETE":
            # Session termination — acknowledge
            return b"application/json", b'{"ok":true}'

        if method != "POST":
            body = _err(None, -32600, f"Unsupported HTTP method: {method}")
            return b"application/json", json.dumps(body).encode()

        # ── Parse JSON-RPC ───────────
        try:
            msg = json.loads(requestBody)
        except (json.JSONDecodeError, ValueError) as e:
            body = _err(None, -32700, f"Parse error: {e}")
            return b"application/json", json.dumps(body).encode()

        # ── Batch ────────────────────
        if isinstance(msg, list):
            responses = []
            for item in msg:
                resp = _dispatch(item)
                if resp is not None:
                    responses.append(resp)
            if not responses:
                # All notifications — spec says 202, but 200 with
                # empty body works for non-browser clients.
                return b"application/json", b"{}"
            body = responses if len(responses) > 1 else responses[0]
            return b"application/json", json.dumps(body).encode()

        # ── Single message ───────────
        method_name = msg.get("method", "?")
        detail = ""
        if method_name == "tools/call":
            tool_name = msg.get("params", {}).get("name", "?")
            detail = f" → {tool_name}"
            mcpStatusMessage(f"MCP: {tool_name}")
        elif method_name not in ("ping", "notifications/initialized", "notifications/cancelled"):
            mcpStatusMessage(f"MCP: {method_name}")
        mcpFileLog(f"MCP <- {method_name}{detail}")
        response = _dispatch(msg)
        if response is None:
            return b"application/json", b"{}"
        return b"application/json", json.dumps(response).encode()


# ─── Start ───────────────────────────────────────────────────

# Stop any previous instance so the script can be re-pasted safely
try:
    mcpLogic.stop()
except NameError:
    pass

MCPRequestHandler._access_allowed = None

mcpLogic = WebServer.WebServerLogic(
    port=2026,
    logMessage=mcpFileLog,
    enableSlicer=False,
    enableExec=False,
    enableStaticPages=False,
    enableDICOM=False,
    enableCORS=True,
    requestHandlers=[MCPRequestHandler(logMessage=mcpFileLog)],
)
mcpLogic.start()

print(f"\n  MCP server: http://localhost:{mcpLogic.port}/mcp")
print(f"  Log file:   {_mcpLogFile}\n")
print("  Configure your MCP client with:")
print(f'    {{"mcpServers": {{"slicer": {{"url": "http://localhost:{mcpLogic.port}/mcp"}}}}}}')
print("\n  To stop: mcpLogic.stop()")
