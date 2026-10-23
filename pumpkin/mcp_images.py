"""Built-in MCP server for pictures: the tool reaches the model as image_generate.

llama-server starts it as `python -m pumpkin.mcp_images`, so the chat's model sees the tool. The chat's own calls
never get here: Pumpkin answers them itself (server.py), because it may have to stop llama-server to make room
while drawing. Calls that do get here (an agent task, another client) ask Pumpkin to draw without stopping it."""
import json
import urllib.error
import urllib.request

from . import config, mcp_base

SHAPES = ["square", "portrait", "landscape", "wide", "tall"]
TOOLS = [
    {"name": "generate", "description": "Draw a picture the user asks for. Write a detailed English prompt: "
                                        "subject, style, setting, light. The user sees the picture.",
     "inputSchema": {"type": "object", "properties": {"prompt": {"type": "string"},
                                                      "shape": {"type": "string", "enum": SHAPES}},
                     "required": ["prompt"]}},
]


def call(name, args):
    if name != "generate":
        raise LookupError(f"Unknown tool {name}")
    s = config.Settings()
    body = json.dumps({"prompt": args.get("prompt", ""), "shape": args.get("shape") or "square",
                       "swap": False}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{int(s['port'])}/pumpkin/api/images/generate", data=body,
                                 method="POST", headers={"Content-Type": "application/json", "X-Pumpkin": "1",
                                                         "Authorization": f"Bearer {config.token()}"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=1800) as r:
            res = json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode() or "{}").get("error")
        except ValueError:
            msg = None
        raise RuntimeError(msg if isinstance(msg, str) else f"Pumpkin answered {e.code}")
    except (urllib.error.URLError, OSError) as e:
        raise RuntimeError(f"Could not reach Pumpkin: {e}")
    return f"The picture is saved as {res.get('path')} in the user's Files ({res.get('model')}, {res.get('seconds')} s)."


def main():
    mcp_base.serve("pumpkin-images", TOOLS, call)


if __name__ == "__main__":
    main()
