# Pumpkin FAQ

Short answers to the questions people ask most. The [guide](GUIDE.md) explains every feature in full.

**Contents**

- [The basics](#the-basics)
- [Hardware and models](#hardware-and-models)
- [Speed](#speed)
- [Privacy and security](#privacy-and-security)
- [Phones and other devices](#phones-and-other-devices)
- [Claude and Pumpkin](#claude-and-pumpkin)
- [Installing, updating, removing](#installing-updating-removing)
- [When something is off](#when-something-is-off)

---

## The basics

**What is Pumpkin?**
A graphical home for [llama.cpp](https://github.com/ggml-org/llama.cpp). It picks and downloads models that fit
your computer, runs them, and gives you a chat with tools, your documents, research, coding, pictures, scheduled
tasks and phone access, all from one app. The models run on your own computer.

**Is it free?**
Yes, for everyone: yourself, your team, your company. You may change it and share it. You may not sell it, or sell a
product or service built mostly on it, without permission (MIT with the Commons Clause, from 0.10.2 on).

**Do I need an account or an internet connection?**
No account. You need the internet to download Pumpkin, llama.cpp and models, and for web search. Chatting, your
documents, coding and pictures work offline once everything is downloaded.

**How is it different from LM Studio or Ollama?**
It uses the same kind of models (GGUF files) and can use the ones LM Studio, Ollama and others already downloaded,
without copying them. On top of running models, it gives the model things to do: search your files with citations,
research the web, write code in your projects, draw, run tasks on a schedule, work in your browser, and be used
from your phone or by Claude.

**Which operating systems?**
Linux (tested on Ubuntu 24.04 and newer), Windows 10 and 11, macOS on Apple Silicon (Intel Macs on the CPU), and
Android 8 or newer on a 64-bit phone. Windows, Mac and Android are marked beta.

## Hardware and models

**What do I need?**
Any 64-bit computer can run small models on the processor. A graphics card makes it much faster:
NVIDIA, AMD and Intel all work (CUDA or Vulkan), and Apple Silicon uses Metal. The more video memory, the bigger
the model.

**Which model should I pick?**
Start with what the Models page recommends: it is picked for your GPU and RAM and marked *fits your GPU*, *runs
with RAM offload* or *works, slowly*. For tools, coding and agents, Qwen3, Qwen3.6, GLM and Devstral do well.
For pictures in the chat, add a small vision model like Gemma 3 4B.

**What do "Q4_K_M", "Q8_0" and the like mean?**
How much the model is compressed. Lower numbers are smaller and faster with a little less quality. Pumpkin picks
the best one that fits, so you rarely need to choose.

**Can I use models I already downloaded?**
Yes. Pumpkin finds LM Studio, Ollama, Hugging Face, llama.cpp, Jan and GPT4All downloads and uses them where they
are. Put other GGUF files in `~/Pumpkin/models` and press *Scan folders again* on the Models page.

**What is a preset?**
A model plus how to run it: context size, tools, web search, thinking budget, unloading when idle. You can have
several presets for the same model.

**What is "context" and how much do I need?**
How much text the model holds at once: your chat, documents and tool results. 8k is fine for chatting, 32k or more
for coding and long documents. More context uses more memory; the preset editor shows whether it still fits.

## Speed

**Why is it slow?**
Usually the model does not fit in video memory, so part of it runs from RAM or on the processor. Pick a smaller
quant, a shorter context, or a MoE model (names with A3B, A22B...), which run well even when they don't fit.
The Overview warns when the model spills into swap or keeps reading itself from disk.

**How do I make it faster?**
Pumpkin already turns on flash attention, picks how many layers go on the GPU, and offers *Faster replies*
(speculative decoding) in the preset. *Reports > Speed* measures a preset, and *Compare* runs several side by side.
Setting *Memory for context* to `q8_0` frees memory for a bigger model or more context.

**Would Pumpkin be faster if it were written in Rust or C++?**
No. The model runs inside llama.cpp, which is already C++. Pumpkin itself only passes text along: about 10
microseconds per word, against the 5 to 20 milliseconds the model needs to write one.

**Can it make 3D models?**
Yes, on Linux and Windows. Install *3D models* on the Images page (about 16 GB), then turn a picture into a textured
`.glb`, or describe an object and the image model draws it first. Add Pixal3D for shapes that follow the picture
more closely, and pick *Best* on a 16 GB card or bigger. See the [guide](GUIDE.md#3d-models).

**My 3D model looks bad. Why?**
Usually the picture: a cropped photo, several objects, a busy background or a person (faces are the hardest part).
Dark clothes can also come out looking like black plastic because the model guesses metal; *Matte surfaces* fixes
that. Use *Clay* in the viewer to see whether the shape itself is good.

## Privacy and security

**Does anything leave my computer?**
Only what you ask for: model and llama.cpp downloads, web searches and pages the model reads, update checks, and
the connectors you set up. No accounts, analytics or crash reports. *Settings > Privacy* lists every connection
and when it happens.

**Who can open my Pumpkin?**
Only someone with its key. Everything needs it, the model server has its own secret key, and other web pages
cannot reach in. Phone access is off until you turn it on, and *Settings > Signed-in devices* signs out a lost
phone.

**Can the model delete my files or run harmful commands?**
Chat tools run commands only in a Docker sandbox that sees just `~/Pumpkin/files`. Pumpkin Code works in your
project folder and asks before changing files or running commands unless you tell it not to. Pumpkin never
changes your documents; it writes only into `~/Pumpkin/files/outputs`.

**What does the model remember about me?**
Facts it picks up from what you say (your name, work, how you like answers) and what you ask it to remember. The
Memory page shows every one; edit, delete, or turn memory off. Passwords and keys are never saved, and the file
stays on your computer.

**Is it safe to let it read web pages and my email?**
Web pages and messages can hide instructions for the AI. Pumpkin treats them as data: research gives the model no
tools at all, connectors start read only, the browser extension asks before it acts, and an unattended task that has
read private data cannot use the web afterwards.

## Phones and other devices

**Can I use it from my phone?**
Yes. On the Phone page pick *Home network* (same Wi-Fi) or *Anywhere* (Tailscale), scan the QR code, and add it to
the home screen. The models still run on your computer.

**Does it work on iPhone and iPad?**
Through phone access, yes: Safari opens the whole app and *Add to Home Screen* makes it feel like one. Running
models on the iPhone itself is not possible, because iOS does not let apps start a downloaded model server.

**Can it run on the phone itself?**
On Android, yes: install the APK from the latest release. Small models (3 to 8B) run on the phone's processor.

**Can my computers work together?**
Yes. Pair them on the Devices page, and your chat can use a model on another device, or one model can hand a job to
a stronger one.

## Claude and Pumpkin

**What does "Use with Claude" do?**
It lets Claude (Claude Code or Claude Desktop) hand jobs to your local model: summaries, pulling facts out of files,
tagging long lists, translations, first drafts. Claude keeps working meanwhile, collects the answers and checks them.
Set it up on the Devices page; the [guide](GUIDE.md#use-with-claude) walks through it.

**Why would I want that?**
It saves Claude's usage on bulk work, runs many small jobs in the background, and keeps private files on your
computer: Claude names the file, Pumpkin reads it here and sends back only the answer.

**Does Claude see my whole computer?**
Only what it asks Pumpkin for: a job, and files by path. It cannot use Pumpkin's other pages, settings or chats. The
key for Claude on another computer opens only the connector, and you can replace it at any time.

**Does it work with claude.ai in the browser or the Claude phone app?**
Not yet. Those only take connectors that sign in with OAuth. Claude Code and Claude Desktop work today.

**Claude's job says it does not fit the model.**
The file or prompt is longer than the preset's context. Raise *Context* in the preset, use a model with a longer
context, or ask Claude to split it (the `batch` tool does one item at a time).

## Installing, updating, removing

**How do I install it?**
Linux and Mac: `curl -fsSL https://raw.githubusercontent.com/geobellingham/pumpkin/main/install.sh | bash`.
Windows (PowerShell): `irm https://raw.githubusercontent.com/geobellingham/pumpkin/main/install.ps1 | iex`.
Android: the APK from the [latest release](https://github.com/geobellingham/pumpkin/releases/latest).

**How do I update?**
Pumpkin checks twice a day and updates in one click (*Settings > Updates*). Running the install command again works
too. llama.cpp updates separately under *Settings > Model server*, by hand or by itself.

**I updated but it still says the old version.**
Run `pumpkin --version` and `which -a pumpkin`. An old copy started by hand or a `PUMPKIN_*` setting left in your
shell can keep the old one running: close it, open a new terminal, and run the install command again.

**How do I remove it?**
Linux and Mac: `~/.local/share/pumpkin/app/uninstall.sh`. Windows: *Apps > Installed apps > Pumpkin > Uninstall*.
Your models, files and reports stay in `~/Pumpkin` (Windows: `%USERPROFILE%\Pumpkin`) until you delete them.

**Can I keep the models on another drive?**
Yes. The first start asks where Pumpkin keeps its files, and *Settings > Folders* changes it later.

## When something is off

**It opens in a browser tab instead of its own window (Linux).**
Install `python3-gi gir1.2-webkit2-4.1`, then open Pumpkin again. If you installed with a Python virtual
environment switched on, run the install command again without it.

**The model does not use tools.**
Turn on *Tools* in the preset and restart the model. Small models are worse with tools; try Qwen3 or GLM.

**"This llama-server is too old for this model."**
Press *Update llama.cpp and try again* on the Overview.

**The model does not start, or runs out of memory.**
Pumpkin already retried with a shorter context. Pick a smaller quant, lower the context, or set *Memory for
context* to `q8_0`.

**The phone cannot connect.**
Home network: same Wi-Fi as the computer. Tailscale: signed in on both. The Phone page shows what is missing.

**Something else.**
Open the Doctor page (or run `pumpkin doctor`). If it does not help, open an
[issue](https://github.com/geobellingham/pumpkin/issues) with what it shows.
