"""Built-in MCP server for artifacts: the model builds a page, document, diagram or piece of code that appears in the
panel next to the chat, and changes it when the user asks.

llama-server starts it as `python -m pumpkin.mcp_artifacts`; the tools reach the model as artifacts_create,
artifacts_update, artifacts_edit, artifacts_read and artifacts_list."""
from . import artifacts, mcp_base

TOOLS = [
    {"name": "create",
     "description": "Make something the user will keep working on next to the chat: a web page (html, one file with "
                    "its own CSS and JS, no outside links), a document (md), a diagram (svg) or code. It opens in a "
                    "side panel. Put the content only in this call, not in your reply.",
     "inputSchema": {"type": "object", "properties": {"title": {"type": "string"}, "content": {"type": "string"},
                                                      "kind": {"type": "string", "enum": list(artifacts.KINDS)}},
                     "required": ["title", "content"]}},
    {"name": "edit", "description": "Change one exact piece of an artifact. Cheaper than update for a small fix.",
     "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}, "find": {"type": "string"},
                                                      "replace": {"type": "string"}}, "required": ["id", "find", "replace"]}},
    {"name": "update", "description": "Replace an artifact's whole content with a new version.",
     "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}, "content": {"type": "string"},
                                                      "note": {"type": "string"}}, "required": ["id", "content"]}},
    {"name": "read", "description": "Get an artifact's current content, including the user's own changes. Read it "
                                     "before changing it.",
     "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}},
    {"name": "list", "description": "The user's artifacts, newest first.",
     "inputSchema": {"type": "object", "properties": {}}},
]


def call(name, args):
    if name == "create":
        a = artifacts.create(args.get("title"), args.get("content"), args.get("kind"), by="model")
        return f"Made {a['kind']} artifact {a['id']} \"{a['title']}\". It is open in the panel next to the chat."
    if name == "list":
        items = artifacts.all_()[:30]
        return "\n".join(f"{i['id']}  {i['kind']}  {i['title']}  (version {i['version']})" for i in items) \
            or "There are no artifacts yet."
    aid = artifacts.find(args.get("id") or args.get("title"))
    if name == "read":
        a = artifacts.get(aid)
        who = "you" if a["by"] == "you" else "the model"
        return f"{a['kind']} artifact {a['id']} \"{a['title']}\", version {a['version']} (last changed by {who}):\n\n" \
               + a["content"]
    if name == "edit":
        a = artifacts.edit(aid, str(args.get("find") or ""), args.get("replace"), by="model")
        return f"Changed it. Now version {a['version']}."
    if name == "update":
        a = artifacts.update(aid, args.get("content"), by="model", note=args.get("note") or "")
        return f"Updated. Now version {a['version']}."
    raise LookupError(f"Unknown tool {name}")


def main():
    mcp_base.serve("pumpkin-artifacts", TOOLS, call)


if __name__ == "__main__":
    main()
