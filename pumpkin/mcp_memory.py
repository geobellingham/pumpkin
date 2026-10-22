"""Built-in MCP server for Pumpkin's memory: the model saves and forgets facts about the user.

llama-server starts it as `python -m pumpkin.mcp_memory`; the tools reach the model as memory_save and
memory_forget. Saved facts stay in memory.json on this device and appear in every new chat."""
from . import mcp_base, memory

TOOLS = [
    {"name": "save",
     "description": "Remember one lasting fact about the user for future chats: their name, work, preferences, "
                    "people and projects they mention, how they want answers. Use it when the user states such a "
                    "fact or asks you to remember something. Do not save passing details, things you guessed, or "
                    "secrets like passwords and card numbers. One short fact per call, written about the user "
                    "(\"Prefers short answers\", \"Works as a nurse\").",
     "inputSchema": {"type": "object", "properties": {
         "fact": {"type": "string", "description": "The fact, one short sentence"}}, "required": ["fact"]}},
    {"name": "forget",
     "description": "Forget something saved earlier, when the user asks you to forget it or says it is no longer "
                    "true. Give the fact in a few words.",
     "inputSchema": {"type": "object", "properties": {
         "what": {"type": "string", "description": "The fact to forget, in a few words"}}, "required": ["what"]}},
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
