"""The small part every built-in MCP server shares: JSON-RPC over stdin and stdout, the way llama-server talks to
its tool servers. A server gives its tools and one function that runs them.

The same loop also runs inside Pumpkin itself on Android (see mcp_local), with a socket in place of stdin/stdout."""
import json
import sys

ERRORS = (OSError, ValueError, LookupError, RuntimeError, PermissionError)


def reply(msg, name, tools, call, errors=ERRORS):
    """The answer to one JSON-RPC message, or None for a notification."""
    mid, method = msg.get("id"), msg.get("method")
    if mid is None:
        return None                                      # notifications need no reply
    if method == "initialize":
        result = {"protocolVersion": (msg.get("params") or {}).get("protocolVersion", "2024-11-05"),
                  "capabilities": {"tools": {}}, "serverInfo": {"name": name, "version": "1"}}
    elif method == "tools/list":
        result = {"tools": tools}
    elif method == "tools/call":
        p = msg.get("params") or {}
        try:
            text = call(p.get("name"), p.get("arguments") or {})
            result = {"content": [{"type": "text", "text": text}], "isError": False}
        except errors as e:
            result = {"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True}
    elif method == "ping":
        result = {}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Unknown method {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def serve(name, tools, call, errors=ERRORS, inp=None, out=None):
    """Answer requests until the input closes. call(tool_name, arguments) returns the text the model sees."""
    inp = inp or sys.stdin
    out = out or sys.stdout
    for line in inp:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if not isinstance(msg, dict):
            continue
        r = reply(msg, name, tools, call, errors)
        if r is not None:
            out.write(json.dumps(r) + "\n")
            out.flush()
