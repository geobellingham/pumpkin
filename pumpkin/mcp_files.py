"""Built-in MCP server for the user's documents: search them, read a page, list them, and save new files.

llama-server starts it as `python -m pumpkin.mcp_files`; the tools reach the model as files_search, files_read,
and files_save. It only sees the folders set in Pumpkin (the work folder ~/Pumpkin/files and any added
under Settings), and only writes inside the work folder."""
from . import config, docs, mcp_base, outputs

TOOLS = [
    # kept short on purpose: every word here is sent with every message, and small models often run with 4-8k context
    {"name": "search", "description": "Search the user's own files (PDF, Word, notes...). Cite results as (file, page).",
     "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}, "file": {"type": "string"}},
                     "required": ["query"]}},
    {"name": "read", "description": "Read a file, or one page of it, e.g. place \"p. 4\".",
     "inputSchema": {"type": "object", "properties": {"file": {"type": "string"}, "place": {"type": "string"},
                                                      "start": {"type": "integer"}}, "required": ["file"]}},
    {"name": "save", "description": "Save a document you wrote for the user. Markdown content; format docx (default), "
                                     "html, md, txt or csv.",
     "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}, "content": {"type": "string"},
                                                      "format": {"type": "string", "enum": list(outputs.FORMATS)}},
                     "required": ["name", "content"]}},
]


def call(name, args, settings=None):
    settings = settings or config.Settings()
    if name == "search":
        if settings["doc_index"]:
            docs.refresh(settings, max_seconds=20)          # files added a moment ago are found too
        n = max(1, min(int(args.get("max_results") or 8), 15))
        return docs.format_hits(docs.search(settings, args.get("query", ""), n, args.get("file") or None))
    if name == "read":
        return docs.read(settings, args.get("file", ""), args.get("place") or None, args.get("start") or 0)
    if name == "list":
        items = docs.listing(settings)
        if not items:
            return ("The user has no documents yet. They can add files on Pumpkin's Files page, or add a folder in "
                    "Settings > Documents.")
        lines = []
        for i in items:
            note = " (could not be read)" if i["unreadable"] else " (a scan or picture, no text)" if i["scanned"] else ""
            lines.append(f"- {i['file']}{note}")
        return f"{len(items)} documents:\n" + "\n".join(lines)
    if name == "save":
        rel = outputs.save(settings, args.get("name"), args.get("content"), args.get("format") or "docx")
        return f"Saved as {rel} in the user's Files (Pumpkin > Files)."
    raise LookupError(f"Unknown tool {name}")


def main():
    mcp_base.serve("pumpkin-files", TOOLS, call)


if __name__ == "__main__":
    main()
