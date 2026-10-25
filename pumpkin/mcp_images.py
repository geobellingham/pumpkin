"""Built-in MCP server for pictures and clips: the tools reach the model as image_generate, image_edit, image_video.

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
    {"name": "edit", "description": "Change the picture the user attached, or the one you drew last: say what to "
                                    "change in a short English instruction (\"make it night\", \"remove the car\"). "
                                    "The user sees the new picture.",
     "inputSchema": {"type": "object", "properties": {"prompt": {"type": "string"}, "source": {
         "type": "string", "description": "Optional: a picture in the user's Files, like images/cat.jpg"}},
                     "required": ["prompt"]}},
    {"name": "video", "description": "Make a short video clip the user asks for (a few seconds; it takes minutes). "
                                     "Write a detailed English prompt: subject, motion, camera, setting, light. Set "
                                     "from_picture to bring the picture the user attached (or you drew) to life.",
     "inputSchema": {"type": "object", "properties": {
         "prompt": {"type": "string"}, "shape": {"type": "string", "enum": SHAPES},
         "seconds": {"type": "number", "description": "Length, 1 to 5 seconds (default 2)"},
         "from_picture": {"type": "boolean"}}, "required": ["prompt"]}},
]


def call(name, args):
    if name not in ("generate", "edit", "video"):
        raise LookupError(f"Unknown tool {name}")
    s = config.Settings()
    body = json.dumps({"prompt": args.get("prompt", ""), "shape": args.get("shape") or ("landscape" if name == "video" else "square"),
                       "source": args.get("source"), "seconds": args.get("seconds"),
                       "from_picture": bool(args.get("from_picture")), "swap": False}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{int(s['port'])}/pumpkin/api/images/{name}", data=body,
                                 method="POST", headers={"Content-Type": "application/json", "X-Pumpkin": "1",
                                                         "Authorization": f"Bearer {config.token()}"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=7200 if name == "video" else 1800) as r:   # as long as Pumpkin waits
            res = json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode() or "{}").get("error")
        except ValueError:
            msg = None
        raise RuntimeError(msg if isinstance(msg, str) else f"Pumpkin answered {e.code}")
    except (urllib.error.URLError, OSError) as e:
        raise RuntimeError(f"Could not reach Pumpkin: {e}")
    if name == "video":
        return f"The video is saved as {res.get('path')} in the user's Files ({res.get('model')}, {res.get('seconds')} s)."
    return f"The picture is saved as {res.get('path')} in the user's Files ({res.get('model')}, {res.get('seconds')} s)."


def main():
    mcp_base.serve("pumpkin-images", TOOLS, call)


if __name__ == "__main__":
    main()
