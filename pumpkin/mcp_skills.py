"""Built-in MCP server for skills: the model reads a skill's instructions when a request fits it.

llama-server starts it as `python -m pumpkin.mcp_skills`; the tools reach the model as skills_read and skills_file.
The list of skills (name and one line each) is in the chat's system message, so the model knows what exists."""
from . import config, mcp_base, skills

TOOLS = [
    {"name": "read", "description": "Read the instructions of one of your skills by its name. Do this before a job "
     "that matches a skill listed in your instructions, then follow it.",
     "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "file", "description": "Read a file that a skill brings along (a template, an example, a reference).",
     "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}, "path": {"type": "string"}},
                     "required": ["name", "path"]}},
]


def _settings():
    try:
        return config.Settings()
    except Exception:  # noqa: BLE001 - the skills still work with everything turned on
        return {}


def call(name, args):
    st = _settings()
    s = skills.find(args.get("name"), st)
    if not s:
        have = ", ".join(x["name"] for x in skills.enabled(st)) or "none"
        raise LookupError(f"There is no skill called {args.get('name')!r}. Skills: {have}.")
    if name == "read":
        return skills.read_text(s)
    if name == "file":
        return skills.read_file(s, args.get("path"))
    raise LookupError(f"Unknown tool {name}")


def main():
    mcp_base.serve("pumpkin-skills", TOOLS, call)


if __name__ == "__main__":
    main()
