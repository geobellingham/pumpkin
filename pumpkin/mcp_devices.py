"""Built-in MCP server that lets the model hand tasks to your other Pumpkins (Devices page).

llama-server starts it as `python3 -m pumpkin.mcp_devices` and talks JSON-RPC over stdin/stdout. The tools show
up as devices_list and devices_ask."""
import json
import sys

from . import devices

TOOLS = [
    {"name": "list",
     "description": "List your other devices running Pumpkin (computers, Macs, phones) that can take tasks: their "
                    "name, whether they are online, the model they are running and the models they have.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "ask",
     "description": "Send a self-contained text task to another device and get its model's answer. It sees only "
                    "the task text, not this chat, so include everything it needs. Use it to split work, get a "
                    "second opinion, or run something on a device better suited for it. Slow devices (phones) can "
                    "take minutes.",
     "inputSchema": {"type": "object", "properties": {
         "device": {"type": "string", "description": "Device name from devices_list"},
         "task": {"type": "string", "description": "The full task, with all the context it needs"},
         "model": {"type": "string", "description": "Optional: a model name on that device (from devices_list). "
                                                    "Leave out to use whatever it runs."}},
         "required": ["device", "task"]}},
]


def _list():
    st = devices.statuses()
    on = [d for d in st if d.get("enabled", True)]
    if not on:
        return "No devices are paired. The user can pair them on the Devices page."
    lines = []
    for d in on:
        info = d.get("info") or {}
        if d.get("online"):
            running = info.get("model") or "nothing yet (starts its default model when asked)"
            lines.append(f"- {d['name']} ({info.get('platform', '?')}): online, running {running}; models: "
                         f"{', '.join(info.get('models') or []) or 'none'}" + (f"; note: {d['note']}" if d.get("note")
                                                                             else ""))
        else:
            lines.append(f"- {d['name']}: offline")
    return "\n".join(lines)


def call(name, args):
    if name == "list":
        return _list()
    if name == "ask":
        r = devices.ask(args.get("device", ""), args.get("task", ""), args.get("model"), sender=_me())
        head = f"[answer from {r.get('device')}, model {r.get('model')}, {r.get('seconds')} s]\n"
        return head + (r.get("answer") or "(empty answer)")
    raise ValueError(f"Unknown tool {name}")


def _me():
    try:
        from . import config
        return devices.device_name(config.Settings())
    except Exception:  # noqa: BLE001
        return ""


def main():
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
            continue
        try:
            if method == "initialize":
                result = {"protocolVersion": (msg.get("params") or {}).get("protocolVersion", "2024-11-05"),
                          "capabilities": {"tools": {}}, "serverInfo": {"name": "pumpkin-devices", "version": "0.1"}}
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                p = msg.get("params") or {}
                try:
                    text = call(p.get("name"), p.get("arguments") or {})
                    result = {"content": [{"type": "text", "text": text}], "isError": False}
                except Exception as e:  # noqa: BLE001 - the model gets the reason and can try something else
                    result = {"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True}
            elif method == "ping":
                result = {}
            else:
                raise LookupError(method)
            reply = {"jsonrpc": "2.0", "id": mid, "result": result}
        except LookupError:
            reply = {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Unknown method {method}"}}
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
