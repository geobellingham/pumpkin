"""The small part every built-in MCP server shares: JSON-RPC over stdin and stdout, the way llama-server talks to
its tool servers. A server gives its tools and one function that runs them."""
import json
import sys


def serve(name, tools, call, errors=(OSError, ValueError, LookupError, RuntimeError, PermissionError)):
    """Answer requests until stdin closes. call(tool_name, arguments) returns the text the model sees."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        mid, method = msg.get("id"), msg.get("method")
        if mid is None:
            continue                                     # notifications need no reply
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
            reply = {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Unknown method {method}"}}
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()
            continue
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid, "result": result}) + "\n")
        sys.stdout.flush()
