# Wishlist

Things wanted for Pumpkin, roughly in the order they are planned. Nothing here is started unless it says so.

## Next (0.7.x)
- **Dark mode toggle.** Light, Dark, System in the top bar and in Settings, remembered, one setting for the chat and the Pumpkin pages.
- **Real-hardware test pass.** Run Pumpkin on the Windows PC with the RX 9070 XT and on the Macs: vision relay with real models, Wan 2.2 downloads, Qwen Image Edit, Pumpkin Code with real local models, restoring a long conversation on a big context, Mac pairing.

## 0.8.0
- **Skills.** Folders with a `SKILL.md` (name, one-line description, instructions) in the same format Claude uses. The model sees only the names and descriptions and loads a skill with a `skill` tool when it fits. A Skills page to import (folder or GitHub link), turn on and off, plus a few built-in ones. Pumpkin Code also reads skills from the project folder.
- **Connectors catalog.** Ready-made MCP servers (GitHub, Google Drive, Notion, Slack, Postgres, a browser and so on) installed with a pasted token, enabled per preset so small models do not see dozens of tools. Writes ask first. API tokens first, sign-in flows later.
- **Artifact preview in chat.** HTML, SVG and charts the model writes show live in a sandboxed frame, with a button to save to Files.

## Later
- **Scheduling from the chat, the way Claude does it.** Today the Tasks page is a form for repeating tasks. The wish is that the model itself schedules work when you ask in plain words ("tomorrow at 03:00 review the code with Qwen3 Coder and write a map"), in the chat and in Pumpkin Code. It works like this:
  - Tools the model can call: `schedule_task`, `list_tasks`, `update_task`, `delete_task` and `remind_me_later`.
  - A task is a one-off time or a repeat, with a complete standalone prompt (the run starts fresh and remembers nothing of the chat), a preset chosen by name, an optional project folder, and an approval mode (ask, edits, auto).
  - Each run is a full session with the same tools as the chat or Pumpkin Code, started on time even when the model has to be loaded first. Pumpkin works out a sensible time when you say "tomorrow" or "tonight", handles the clock change, and tells you the exact time it picked.
  - The result goes to Reports, and a notification (ntfy and desktop) says it finished or failed. In Pumpkin Code the run can work on its own git branch and never push.
  - `remind_me_later` sends a message back into the same conversation at the time you set, so a long job can say "check on this in 20 minutes".
  - The Tasks page shows and edits everything the model scheduled, including one-off tasks and their past runs.
  - Safety: the model asks you to confirm before creating an unattended task that edits files or runs commands, and says clearly which approval mode it will run in.
- **Workspaces.** Pumpkin Code for any folder and any kind of work (files, reports, spreadsheets). A workspace is a folder plus its instructions, skills, connectors and memory, with the same approvals and undo.
- **Helper agents.** A `delegate` tool that hands a research job to a fresh session with its own context and gets a summary back. Needs two sessions at once in llama-server, so measure the speed cost first.
- **Plugins.** A bundle of skills, connectors, a persona and prompts, installed from a GitHub link.

## Smaller ideas
- Measure speculative decoding and make it a proper toggle (today it is only a preset option).
- Model comparison page.
- Overview notice when the model is swapped out or reading from disk.
- Restart Pumpkin by itself after pairing.
- Windows code signing through SignPath (waiting for the application).
- Phone "Load failed" on long tool calls.
