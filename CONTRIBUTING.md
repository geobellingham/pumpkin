# Contributing to Pumpkin

Thanks for helping. Pumpkin aims to be the easiest way to run AI agents on your own computer, so the
bar for every change is: would someone who has never heard of GGUF understand what just happened?

## Layout

| Path | What it is |
|---|---|
| `pumpkin/` | The Python backend (standard library only): server manager, model library, agent runs, API |
| `pumpkin/catalog.json` | The one-click model list |
| `web/` | The web UI: Pumpkin's pages plus a small patch on top of the llama.cpp UI (see `web/README.md`) |
| `tests/` | Backend tests, with a fake llama-server and tiny GGUF files |

## Running from a checkout

```sh
./web/build.sh                 # once, needs Node.js 20+ and git
python3 -m pumpkin serve       # http://127.0.0.1:8090, sign in with: python3 -m pumpkin link
python3 -m unittest discover -s tests
```

`PUMPKIN_FAKE_HARDWARE=hw.json` pretends to have other hardware, and `PUMPKIN_HF_URL` points model
search and downloads at another server. `tests/fake_hf.py` is a small fake Hugging Face for UI work.

## Good first contributions

- **Catalog:** add or update models in `pumpkin/catalog.json`. Include the repo, a one-line plain
  description and the smallest memory it runs well in.
- **Hardware:** fit estimates for more architectures in `pumpkin/models.py`.
- **Doctor:** checks for problems you hit while setting up.
- **Translations of the UI text.**

## Style

- Python: standard library only, readable over clever, errors that tell people what to do next.
- UI: reuse the llama.cpp components (`$lib/components/ui`), keep Pumpkin code inside
  `web/src/lib/pumpkin`, and keep upstream hooks to one or two lines.
- Words: plain and specific. Buttons say what happens ("Install", "Save preset").
