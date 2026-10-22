"""Built-in MCP server for the user's documents: search them, read a page, list them, and save new files.

llama-server starts it as `python -m pumpkin.mcp_files`; the tools reach the model as files_search, files_read,
files_list and files_save. It only sees the folders set in Pumpkin (the work folder ~/Pumpkin/files and any added
under Settings), and only writes inside the work folder."""
from . import config, docs, mcp_base, outputs

TOOLS = [
    {"name": "search",
     "description": "Search the user's own documents (PDFs, Word, slides, spreadsheets, notes, emails) on this "
                    "device. Use it whenever the question may be answered by their files: contracts, reports, "
                    "invoices, manuals, notes, letters. Returns short passages with the file and page. Answer from "
                    "these passages and cite them as (file, page). Search again with other words if nothing fits.",
     "inputSchema": {"type": "object", "properties": {
         "query": {"type": "string", "description": "Key words to look for: names, numbers, terms from the question"},
         "file": {"type": "string", "description": "Only search this one document (optional)"},
         "max_results": {"type": "integer", "description": "How many passages (1-15, default 8)"}},
         "required": ["query"]}},
    {"name": "read",
     "description": "Read a document, or one page, slide or sheet of it, when the passages from search are not "
                    "enough. Use the file name and place exactly as search or list gave them.",
     "inputSchema": {"type": "object", "properties": {
         "file": {"type": "string", "description": "The document, as named by search or list"},
         "place": {"type": "string", "description": "A page, slide or sheet, like \"p. 4\" (optional: whole file)"},
         "start": {"type": "integer", "description": "Character offset to continue a long read (default 0)"}},
         "required": ["file"]}},
    {"name": "list",
     "description": "List the user's documents that can be searched, newest first.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "save",
     "description": "Save something you made for the user as a file they can open and send: a report, letter, "
                    "summary, table or plan. Write the content in Markdown (headings, lists, tables, **bold**). "
                    "format: docx (Word), html (web page), md, txt or csv (for a table, write CSV). The file appears "
                    "on Pumpkin's Files page, in the outputs folder. Tell the user the file name after saving.",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string", "description": "File name without the extension, like \"Lease summary\""},
         "content": {"type": "string", "description": "The full content"},
         "format": {"type": "string", "enum": list(outputs.FORMATS), "description": "File type (default docx)"}},
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
