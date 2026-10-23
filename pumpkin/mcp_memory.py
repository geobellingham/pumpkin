"""Built-in MCP server for Pumpkin's memory: the model saves and forgets facts about the user.

llama-server starts it as `python -m pumpkin.mcp_memory`; the tools reach the model as memory_save and
memory_forget. Saved facts stay in memory.json on this device and appear in every new chat."""
from . import mcp_base, memory

TOOLS = [
    {"name": "save", "description": "Remember a lasting fact the user told you about themselves. Never secrets.",
     "inputSchema": {"type": "object", "properties": {"fact": {"type": "string"}}, "required": ["fact"]}},
    {"name": "forget", "description": "Forget a saved fact when the user asks.",
     "inputSchema": {"type": "object", "properties": {"what": {"type": "string"}}, "required": ["what"]}},
]


def call(name, args):
    if name == "save":
        item = memory.add(args.get("fact", ""), source="chat")
        return f"Saved to memory: {item['text']}"
    if name == "forget":
        gone = memory.forget(args.get("what", ""))
        if not gone:
            return "Nothing in memory matched that. Nothing was removed."
        return "Forgot: " + "; ".join(i["text"] for i in gone)
    raise LookupError(f"Unknown tool {name}")


def main():
    mcp_base.serve("pumpkin-memory", TOOLS, call)


if __name__ == "__main__":
    main()
