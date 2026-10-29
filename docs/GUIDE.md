# Pumpkin guide

Everything Pumpkin can do, and how to use each part. For installing, see the [README](../README.md#install).

Pumpkin opens on the chat. The grid icon on the left (or the status chip at the top right) opens
Pumpkin's own pages: Overview, Models, Presets, Agent, Code, Tasks, Files, Images, Memory, Prompts,
Reports, Voice, Devices, Phone, Doctor and Settings.

**Contents**

- [Getting started](#getting-started)
- [The Overview: what is running right now](#the-overview-what-is-running-right-now)
- [Setup list](#setup-list)
- [Models](#models)
- [Presets](#presets)
- [Chatting](#chatting)
- [Tools: web search, files and more](#tools-web-search-files-and-more)
- [Your documents](#your-documents)
- [Pictures a model can't see](#pictures-a-model-cant-see)
- [Drawing pictures](#drawing-pictures)
- [Editing pictures](#editing-pictures)
- [Short videos](#short-videos)
- [Pumpkin Code](#pumpkin-code)
- [Workspaces](#workspaces)
- [Working in your browser](#working-in-your-browser)
- [Skills](#skills)
- [Connectors](#connectors)
- [Memory](#memory)
- [Personas, agent limits and MCP servers](#personas-agent-limits-and-mcp-servers)
- [Tasks on a schedule](#tasks-on-a-schedule)
- [Saved prompts](#saved-prompts)
- [Reports and the speed test](#reports-and-the-speed-test)
- [Files](#files)
- [Voice](#voice)
- [Using it from your phone](#using-it-from-your-phone)
- [Devices working together](#devices-working-together)
- [Doctor](#doctor)
- [Settings](#settings)
- [Commands](#commands)
- [Where things are kept](#where-things-are-kept)
- [When something goes wrong](#when-something-goes-wrong)

---

## Getting started

The first start walks you through four steps:

1. **Your computer.** Pumpkin shows your GPU, video memory and RAM, and asks where to keep its files: pick
   a drive (say D:) and models, image models, your files, reports and saved conversations all go into a
   Pumpkin folder there. You can change it later in Settings; what is already in the old folder stays there and
   keeps working. If LM Studio, Ollama, the Hugging Face
   cache, Jan or GPT4All already downloaded models, tick the ones to use. They are used where they are,
   nothing is copied.
2. **What for.** Chat, coding, research or agents. This sets the starting persona and which models are
   recommended.
3. **Model server.** Pumpkin downloads the newest llama.cpp build for your GPU (CUDA or Vulkan for NVIDIA,
   Vulkan for AMD and Intel, Metal on a Mac, CPU otherwise).
4. **Your model.** Pick one you have or install the recommended one. It downloads, starts and opens the chat.

## The Overview: what is running right now

The Overview page is the dashboard.

- **The running model**: its name and settings, the speed of the last answer with a small graph of the last
  few minutes, how fast it reads a prompt, how much it has written, the longest chat it held, and how long
  it has been running.
- **Memory**: video memory and RAM, each with a graph, and how busy the graphics card is. RAM shows what
  your system's own monitor shows. On Linux, the part of the model file the system keeps in RAM as cache is
  drawn as a lighter segment next to it: the system counts it as free and gives it back when something needs
  it.
- **Right now**: one line for everything Pumpkin is working on. The model (loading, answering, or paused so a
  picture has room), the vision model, the image model (loading, drawing, making a video), downloads, installs,
  scheduled tasks and Pumpkin Code sessions, each with how long it has been at it. The model's line also
  shows how full its memory is for your chat.
- **Warnings**: on Linux, when part of the model was moved to swap, or when it keeps reading itself from disk
  while it writes (it does not fit in free RAM), so you know why it is slow. When the model stops because
  llama.cpp is too old for it ("unknown model architecture"), **Update llama.cpp and try again** gets the
  newest one and starts the model again.

## Setup list

Below that, **Set up Pumpkin** lists what makes Pumpkin better once it is set up: the engine, a model, seeing
pictures, drawing, editing and video, searching your documents, Pumpkin Code, a scheduled task, phone access
and notifications. Each step opens the page where you do it. **Skip** anything you do not need, and **Show
skipped** brings it back. **Hide** removes the list from the Overview; **Show the setup list** puts it back.
The first three steps (engine, model, preset) cannot be skipped.

## Models

**Models page.** Three parts:

- **Recommended.** Models picked for your hardware, each with a fit label: *fits your GPU*, *runs with RAM
  offload* (slower), *works, slowly* (processor only) or *too big*. Click **Install** and Pumpkin picks the
  best quant that fits, downloads it, makes a preset and can start it.
- **Find more models.** Search any GGUF on Hugging Face. Every file gets the same fit label, so you can see
  which quant to take before downloading.
- **Models on this computer.** Everything Pumpkin found, with its size. **Scan folders again** after you add
  files by hand. Deleting a model that is running stops it first.

Tips:
- MoE models (names with A3B, A22B...) run well even when they don't fit the GPU, because only a small part
  is used for each word.
- A model with "VL" or a vision projector (mmproj file) can see pictures you attach.
- Models that need a license accepted on huggingface.co need your Hugging Face token (Settings).

## Presets

A preset is a model plus how to run it. **Presets page > Edit** on a preset:

| Setting | What it does |
|---|---|
| Context | How much text the model can hold at once (your chat, documents, tool results). More context uses more memory. The estimate below shows whether it still fits. |
| Memory for context | The KV cache type. `q8_0` halves its memory with almost no loss; `q4_0` saves more but can hurt quality. |
| Vision file | The mmproj file that lets the model see pictures. Each file shows the folder it is in; the one made for the chosen model comes first and is marked. Pumpkin fills it in when it finds one, and switches it when you pick another model. |
| Tools | Gives the model your documents, memory, saving files, your devices, and (with Docker) commands in a sandbox. Turn this on for agents and tasks. |
| Web search | Built-in web search and page reading. No API key. |
| Faster replies | Speculative decoding: the model guesses the next words from what is already in the chat and checks several at once. Same answers, faster when text repeats (quotes, code edits, summaries). Off for presets with a Vision file. It can cost speed on some models: **Reports > Speed > Faster replies > Measure** tries it on and off and the result shows here. |
| Thinking budget | How long a thinking model may think before it answers. |
| Unload when idle | Frees the GPU after this many minutes without use. |
| GPU headroom | Memory kept free on the card for other programs. |
| Extra llama-server options | Any other llama-server flag, for experts. |

Start a preset with its play button, from the status chip, or just pick it in the chat's model list.

If a model runs out of memory while loading, Pumpkin starts it again with a shorter context and keeps the
setting that worked. If it crashes later, Pumpkin starts it again.

## Chatting

The chat is the llama.cpp web UI. Some things Pumpkin adds:

- **Switch models** from the model list next to the send button. Pumpkin stops the current model and starts
  the new one. Models on your paired devices show up here too, like *Qwen3 27B (on Gaming PC)*.
- **Long chats survive restarts.** When a model stops (or pauses to draw a picture), Pumpkin saves what it
  has read and loads it back next time, so a long chat doesn't have to be read again. Settings >
  *Remember the conversation across restarts*.
- **Long chats are summarised, not stopped.** When a chat no longer fits the model's memory, Pumpkin sends
  the model a summary of the older messages and the newest ones in full. The chat on screen keeps every
  message; the Overview shows when it happened. Settings > *Summarise long chats instead of stopping*.
- **Preview pages and drawings.** When the model writes a web page or an SVG, the eye button on the code block shows
  it full screen. **Save to Files** keeps it in Files > artifacts, the download button saves it anywhere.
- **Attach files and pictures** with the paperclip. Pictures work with any model (see
  [Pictures a model can't see](#pictures-a-model-cant-see)).

## Tools: web search, files and more

Turn on **Tools** in a preset (and **Web search** if you want it), then restart the model. In the chat,
just ask. The model decides when to use a tool, and each call shows in the chat. What it can do:

| Ask for | Tool it uses |
|---|---|
| "What's new with llama.cpp this week?" | Web search and page reading |
| "What does my lease say about notice?" | Your documents ([below](#your-documents)) |
| "Write this up as a Word file" | Saving files to `~/Pumpkin/files/outputs` (Word, web page, Markdown, CSV) |
| "Remember that I work nights" | Memory |
| "Draw a fox in the snow" | Pictures (with an image model installed) |
| "Ask my Mac's model for a second opinion" | Devices (with a paired device) |
| "Run this Python and plot the result" | The Docker sandbox (needs Docker) |

Small models are less reliable with tools. Qwen3 and Qwen3.6, GLM, Gemma 3 and Devstral do well.

## Your documents

The model can search your PDFs, Word files, slides, spreadsheets, notes and emails, and says where each
fact came from, like *(lease.pdf, p. 4)*.

1. Put files in `~/Pumpkin/files`, or **Files page > Your documents > add a folder** to use one you already
   have. Pumpkin reads them on your computer and keeps watching for changes.
2. Start a model with **Tools** on.
3. Ask: "What does my lease say about the notice period?" or "Summarise the bank PDFs and save it as a Word
   file."

The search box on the Files page tries a search without the model.

**Scans and photos of paper.** Files with no text (a scanned PDF, a photo of a receipt) are counted as
"pictures or scans without text". With a vision model installed, press **Read scans**: the vision model
copies the text from each page so the search can find it. It takes a while for many pages.

Pumpkin never changes your own files. It only writes into `~/Pumpkin/files/outputs`.

## Pictures a model can't see

Attach a picture to a chat with any model. If the model can see (it has a vision file), the picture goes
straight to it. If it can't (for example Qwen3 80B), a small vision model looks first and tells it what's
there, text included.

1. Install a small vision model on the Models page, for example **Gemma 3 4B** (it comes with its vision
   file).
2. Check **Settings > Let text-only models see pictures** is on (it is by default).
3. Chat with your text-only model, attach a picture, ask about it.

If both models don't fit in memory together, the chat model pauses for a few seconds while the picture is
read, then comes back by itself. The vision model frees its memory after 5 idle minutes.

The chat model gets a description of the picture, so "what does this say" and "what's in this photo" work
well, while fine visual detail can get lost. For that, use a model that sees for itself.

## Drawing pictures

1. **Images page > install the engine** (stable-diffusion.cpp, the right build for your GPU).
2. Install an image model: **Z-Image Turbo** for photos, art and readable text, or **SDXS (tiny)** for
   computers without a graphics card.
3. Try it on the Images page, or in a chat with Tools on: "draw a red fox in the snow, watercolour".

The picture shows in the chat and is saved in `Files/images`. Shapes: square, portrait, landscape, wide,
tall (ask for them in words).

**Memory.** If the text model and the image model don't fit together, the text model pauses while the
picture is drawn and comes back by itself. Images page > Memory sets this to *When needed* (default),
*Always* or *Never*. The image model frees its memory after a few idle minutes.

## Editing pictures

Install **Qwen Image Edit** on the Images page (about 20 GB with its parts). Then, in a chat with Tools on:

- Attach a photo and say what to change: "make it night", "remove the car", "change the sign to say OPEN".
- Or change a picture it just drew: "now add snow".

The new picture is saved in `Files/images` with a name starting with `edit-`. It's a big model, so expect
minutes, and on most graphics cards part of it runs from RAM.

## Short videos

Install **Wan 2.2 (5B)** on the Images page. Then, in a chat with Tools on:

- "Make a 3 second video of a pumpkin rolling down a hill."
- Attach a picture and say "make a video of this, the camera slowly moves in" to bring it to life.

The clip plays in the chat and is saved in `Files/videos`. Clips are 1 to 5 seconds (2 by default). It is
slow: a few seconds of video take several minutes even on a good graphics card, and the text model pauses
meanwhile if memory is short.

## Pumpkin Code

A coding agent that works in a project folder: it reads the code, changes files, runs commands and tests,
and keeps going until the job is done.

**In the app:** Code page.

1. Pick the project folder (type it, use **Browse**, or click a recent one).
2. Pick how much it may do on its own:
   - **Ask first** (default): it asks before every file change and every command.
   - **Edit freely**: changes files without asking, asks before commands.
   - **Never ask**: works on its own. Risky commands (sudo, git push, deleting folders, git reset --hard)
     still ask.
3. Pick the model, write the job ("fix the failing test", "add a dark mode toggle to settings") and
   **Start**.

While it works you see what it reads, each change as a diff, each command with its output, and its plan.
When it asks, answer **Yes**, **Yes to all** (for this session), or type what to do instead and press **No**.
**Undo** takes back the last file change (press again for the one before). The square button stops it.
A bar above the conversation says what the model is doing right now (reading, thinking, writing, running a
command, waiting for you), for how long and how many tokens (read from the model server itself, so it keeps counting while a long file is being written; if it stops for 90 seconds it warns you; if the model server goes idle while Pumpkin Code is still waiting for an answer, Pumpkin Code notices and asks again), and the header shows how full the model's memory
is. There is no limit on how long one answer may be (Agent page > *Longest answer in Pumpkin Code*, 0 = as long as
it needs). If a model runs out of context in the middle of a file, Pumpkin Code asks it to send the file in parts
and goes on by itself.

**In a terminal:** go to the project folder and run

```sh
pumpkin code                       # start, then type the job
pumpkin code "fix the failing test"
pumpkin code -c                    # continue the last session in this folder
pumpkin code -m coder              # use a preset whose name contains "coder"
pumpkin code --mode edits          # ask, edits or auto
```

At the `>` prompt, type the job. Answer questions with `y` (yes), `a` (always) or `n`, or type what to do
instead. Ctrl+C stops it. Commands: `/mode ask|edits|auto`, `/undo`, `/todo`, `/model NAME`, `/new`,
`/help`, `/quit`.

Sessions are shared: one started in the terminal shows on the Code page, so you can follow it and answer
from your phone. With notifications on, you get one when it is waiting for you.

Tips:
- Put an `AGENTS.md` (or `CLAUDE.md`, `PUMPKIN.md`) in the project with how to build and test it. Pumpkin
  Code reads it first.
- Use a model that is good at tool calls (Qwen3 Coder 30B A3B, Qwen3.6, GLM, Devstral) and at least 32k of
  context. Long sessions are summarised by themselves when they get close to the limit.
- Its file tools only work inside the project folder. Commands are different: they run on your computer as you
  (not in the Docker sandbox) and can reach anything you can, which is why it asks first by default. In
  **Never ask** mode only the obviously risky commands still ask, so use it only with a model and a project you
  trust.

## Workspaces

A workspace is a folder Pumpkin Code keeps coming back to, with its own settings. It is not only for code:
pick *Documents and writing*, *Spreadsheets and data* or *Any files* and Pumpkin Code works the same way on
reports, notes and tables.

**In the app:** Code page > *Workspaces* (above the folder picker). **New workspace**, then fill in:

- **Folder** and **kind**. The kind changes how the model is told to work (code: tests and small diffs;
  writing: keep your voice and structure; data: check numbers by running them).
- **Instructions**: always applied, like an `AGENTS.md` that lives in Pumpkin instead of in the folder.
- **Memory**: what the model learned about this work. It adds to it with the `remember` tool, and you can edit it.
  Notes that read like orders to the model are not kept, and the model is told the notes are background, not
  instructions.
- **Connectors**: the apps from the Connectors page this workspace may use (their tools show up in the session
  and ask first; a tool that says it only reads skips the question, except in *Ask first*).
- **Skills**: all of them, or only some. **Model** and **approval mode**: the defaults for new sessions here.

It can read `.docx`, `.xlsx` and `.pptx` files (text, sheets and slides). It does not write them.
In a terminal: `pumpkin code -w NAME`.

**Helper agents.** When a job needs a lot of reading (a big folder, many pages), the model can send a helper.
The helper starts with an empty context, can only read and search, and returns a short summary. You see
*Helper* lines in the session. A helper uses the same model, one request at a time, so it saves context
rather than time.

## Working in your browser

The Pumpkin extension (Chrome, Edge and other Chromium browsers) lets the model on your computer use your own
browser: open pages, read them, click, type, scroll. It uses your logins and your tabs. Nothing leaves your
computer except what the pages themselves send.

**Set it up:** the Browser page (left menu). Download the zip, unzip it, open `chrome://extensions`, turn on
Developer mode, **Load unpacked**, pick the folder. Click the Pumpkin icon, make a pairing code on the Browser
page (it works once, for ten minutes) and type it in the panel.

**Use it:** keep the panel open (it is how Pumpkin reaches the browser) and tell it what to do: "find the
cheapest flight on this page", "fill in this form with the details from my notes". A new chat starts in the tab
you are looking at; the tabs it opens are its tabs too. It cannot see your other tabs.

**What keeps it safe:**

- The first time it wants a site you are asked: *Allow this chat*, *Always allow* or *No*. Chrome asks you
  as well, once per site. Banks, mail, password managers and anything on your own computer or home network
  (your router, a NAS, `.local` names) are always asked about and never kept. Manage kept sites on the Browser
  page.
- Pumpkin's own page (and its model server) can never be used from the browser, even if you say yes.
- *Approval* in the panel's settings: **Ask before each action** (you see what it is about to click or type),
  **Ask once for each site**, or **Never ask**.
- It never types into password fields or card number and security code fields. If a page needs a login or a
  payment, it asks you to do that part.
- When it asks before a click, you see the button's label and what it actually says on screen, so a button
  labelled "Next" that says "Delete my account" shows both.
- Text on a page is data, never orders. If a page contains something that reads like instructions to the AI
  ("ignore your previous instructions..."), Pumpkin warns the model and asks you before every action until you
  write your next message, even in *Never ask* mode.
- A browser chat only has the browser tools: it cannot read or change your files or run commands, whatever a
  page tells it.
- The paired browser's key only opens the browser endpoints, not your files, chats or settings. Remove a
  browser on the Browser page and it stops working at once.

Tips: a model that is good at tool calls works best, and pages with lots of text need a larger context.
Pages inside frames, canvas apps (maps, design tools) and the browser's own pages (`chrome://`) cannot be read.

## Skills

A skill is a set of instructions for a particular kind of job, like writing a report, summarising a document or
triaging security logs. Skills use the same format as Claude's: a folder with a `SKILL.md` that starts with a
`name` and a one-line `description`, then the instructions, plus any files it needs (templates, references).

The model only sees each skill's name and description, which costs almost nothing even on a small model. When a
request fits one, it reads the instructions with the `skills_read` tool and follows them. Skills work in the chat
(with a preset that has tools on), in scheduled tasks and in Pumpkin Code.

**Skills page:**
- Pumpkin comes with a few: *write-a-report*, *summarise-a-document* and *review-security-logs*.
- **New** writes your own. Keep the description about *when* to use it, since that line is all the model sees until
  it opens the skill.
- **Import** takes a GitHub link (one skill folder, or a whole repository of skills) or a folder on your computer.
  Skills written for Claude work as they are.
- Switch any skill off, and switch skills off altogether with **Use skills** (restart the model after that one).
- A built-in skill can't be changed, but **Save as my skill** under a new name makes your own version.

Pumpkin Code also reads skills from the project itself: `.pumpkin/skills`, `.claude/skills` or `.agents/skills`.

## Connectors

Ready-made tool servers for apps and services. The model gets their tools the next time it starts.

| Connector | What it does | Needs |
|---|---|---|
| GitHub | repositories, issues, pull requests, Actions (read only unless you switch that off) | Docker, a personal access token |
| Notion | search, read and update pages and databases | Node.js, an integration token |
| Browser | a real browser the model can click and type in | Node.js |
| Library docs (Context7) | current documentation for programming libraries | Node.js |
| Folders | read and write files in folders you pick | Node.js |
| Git | status, log, diffs and commits of a repository | uv |
| Time and time zones | today's date and time, time zone conversions | uv |
| Jira and Confluence | issues and pages, Cloud or Server | uv, an API token |
| PostgreSQL | tables and queries (read only unless you allow changes) | uv, the database address |
| SQLite | query a database file | uv |

**Connectors page:** **Set up**, fill in what it asks for, **Test** (it starts the server, asks for its tools and
stops it again; the first test downloads it, which can take a minute), then switch it on. **Restart** the model to
give it the new tools. Tokens stay on your computer in Pumpkin's settings folder.

A preset uses every connector that is on, or only the ones you tick (Presets > edit > *Every connector that is on*).
Small models get worse with dozens of tools, so give each preset only what it needs. The chat asks you before each
tool call, as it does for every tool. Your own MCP servers can still be added by hand on the Agent page.

## Memory

Tell the model things about you ("I'm a nurse", "keep answers short") and it saves them. Every new chat
starts with them. **Memory page**: see every fact, edit, forget, add your own, or switch memory off. It is
one file on your computer. The model is told never to save passwords or card numbers.

## Personas, agent limits and MCP servers

**Agent page:**

- **Personas** are the system prompt every chat and task starts with. Pick one, edit them, or add your own.
- **Agent limits**: how many tool steps a task may take, and how much of each tool result is kept.
- **MCP servers**: add extra tools in the same JSON format as Cursor and Claude Desktop (for example the
  filesystem or GitHub servers). Restart the model to load them. They run as you, outside the sandbox.

## Tasks on a schedule

Agent jobs that run by themselves, like "every weekday at 8:00, brief me on the news".

1. **Tasks page > Start from a template** (news, security, weather, price watch, a daily lesson) or make
   your own. Words in angle brackets need your details.
2. Set the time, days and preset. Tick *Stop the model afterwards* to free the GPU when it's done.
3. Save. **Run** tries it straight away.

Each run saves a Markdown report (Reports page) and can notify your phone. For tasks to run with the
window closed, turn on **Settings > Start Pumpkin when I log in**.

## Saved prompts

**Prompts page**: prompts you use often. Put one in the chat box, or run it in the background as a task.

## Reports and the speed test

**Reports page**: every task run, readable and deletable. The **speed test** measures how fast the running
model reads and writes, so you can compare presets (context sizes, quants). **Faster replies > Measure** runs it
with speculative decoding on and off for the running preset (the model restarts twice and comes back as it was)
and says which is faster.

**Compare** asks two to four presets the same question and shows the answers side by side with their speed
and load time. They load one after another, the answers are saved as a report, and the preset that was running
starts again at the end.

## Files

**Files page**: the AI's work folder, `~/Pumpkin/files` (the sandbox sees it as `/workspace`). View,
download, upload and delete files, from your phone too. Pictures are in `images`, videos in `videos`,
documents the model writes in `outputs`. Files the AI made are only downloaded, never opened as pages
inside Pumpkin.

## Voice

Tap the microphone in the chat for hands-free voice chat: you talk, it answers out loud, sentence by
sentence as the answer streams. **Voice page**: language, and *Read typed replies aloud*.

## Using it from your phone

**Phone page**, then pick:

- **Home network**: nothing to install, works on your Wi-Fi. Not encrypted, so only on a network you trust.
- **Anywhere (Tailscale)**: encrypted and works away from home. Pumpkin ticks off each setup step as you do
  it.

Scan the QR code with the phone's camera. It signs the phone in (the code works once, for ten minutes).
*Share > Add to Home Screen* gives you an app icon.

**Phone notifications**: install the free ntfy app, subscribe to a topic, paste the topic on the Phone page.
Tasks then push a notification when they finish.

Pumpkin also runs on Android by itself, models included. See the [README](../README.md#android-beta).

## Devices working together

Pair the Pumpkins on your PC, Mac and phone, and one model can use the others.

1. On the helper: **Devices > Let my other devices use this one**, copy the code.
2. On the device you chat on: **Devices > Add a device**, paste the code.
3. The helper's models appear in your chat's model list. Pick one to chat with it directly.
4. The running model gets `devices_list` and `devices_ask` when it starts, so if it has **Tools** on, Pumpkin
   restarts it by itself once nobody is using it. Then it can hand work to a device by itself, or when you ask:
   "have my Mac write the first draft, then check it".

The devices need to reach each other: same Wi-Fi, or Tailscale on both. A helper only gives answers: no
access to your files, settings or chats. **Make a new code** cuts off every device paired so far.

## Doctor

**Doctor page** (or `pumpkin doctor` in a terminal): checks the GPU driver, Vulkan or CUDA, Docker, disk
space, Tailscale and more, and gives the command that fixes each problem.

## Settings

**Appearance**: light, dark or the same as your system, for the chat and Pumpkin's pages. The sun or moon
button at the top of Pumpkin's pages switches it too. It's kept per device.

- **Chat settings**: sampling, display and tools in the chat.
- **Folders**: where models are found, where new ones download, where reports go.
- **Behaviour**: keep the computer awake while a model is loaded, remember conversations across restarts,
  let text-only models see pictures, start the last model automatically, notifications, start Pumpkin at
  login.
- **Performance and network**: CPU threads, ports.
- **Hugging Face token**: only for models that need a license accepted.
- **Signed-in devices**: see every browser and phone signed in, sign one out, or make a new access key.
- **Model server**: the llama.cpp in use, and **Update llama.cpp**. New models often need a newer one. **Keep
  llama.cpp up to date**: off, tell me (the Overview mentions it once yours is a week behind), or by itself (at
  most once a day, while no model runs). **Which builds**: the latest release that has been out for a day, or the
  newest build for brand-new models. The build before stays on disk, with **Go back**. The first time a new build
  runs, Pumpkin checks it still answers the way the chat expects; if it doesn't, it goes back by itself and skips
  that build. A build whose GPU code does not compile on your system (a Mac with an older macOS, say) is undone the
  same way and skipped, with newer ones, for a week; one that needs a newer Linux or macOS to start is skipped for a
  month. A llama-server you built yourself is never replaced: Pumpkin only says when the one it downloaded
  is newer.
- **Updates**: Pumpkin checks GitHub twice a day and updates in one click.
- **Privacy**: the full list of what Pumpkin sends over the network and when. No accounts, analytics or
  crash reports.

## Commands

```text
pumpkin                     open Pumpkin (starts it in the background if needed)
pumpkin code                Pumpkin Code in this folder (see above)
pumpkin link                print the sign-in links for this computer and your phone
pumpkin doctor              check the setup
pumpkin service install     start Pumpkin when you log in
pumpkin stop                stop Pumpkin and the model
pumpkin --version           which version you have
```

## Where things are kept

| What | Linux and Mac | Windows |
|---|---|---|
| Settings, memory, access key | `~/.config/pumpkin` | `%APPDATA%\pumpkin` |
| Models, files, reports | `~/Pumpkin` | `%USERPROFILE%\Pumpkin` |
| llama.cpp, image engine, saved conversations, Code sessions, document index | `~/.local/share/pumpkin` | `%LOCALAPPDATA%\pumpkin` |
| Logs | `~/.local/state/pumpkin` | `%LOCALAPPDATA%\pumpkin\state` |

## When something goes wrong

| Problem | Try |
|---|---|
| "This llama-server is too old for this model" | **Update llama.cpp and try again** on the Overview. If you built llama.cpp yourself, pull and rebuild it. |
| The model won't start / out of memory | Pumpkin already retried with a shorter context. Pick a smaller quant, lower the context, or use `q8_0` memory for context in the preset. |
| The model doesn't use tools | Turn on **Tools** in the preset and restart the model. Try a model that is better with tools. |
| No picture: "not enough free memory" | Images page > Memory > *Always*, so the text model always pauses while drawing. |
| A paired device's model doesn't call `devices_ask` | Check **Tools** is on in the preset. Pumpkin restarts the model by itself after pairing once it is idle; **Devices > Restart the model** does it now. |
| The phone can't connect | Same Wi-Fi in home-network mode; with Tailscale, check both are signed in. The Phone page shows what's missing. |
| Anything else | The Doctor page. If it doesn't help, open an issue on GitHub with what it shows. |
