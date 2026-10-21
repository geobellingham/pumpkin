# Pumpkin web UI

Pumpkin's interface is the [llama.cpp](https://github.com/ggml-org/llama.cpp) chat UI with Pumpkin's
pages added. Everything Pumpkin-specific lives in two folders, so following upstream stays easy:

- `src/lib/pumpkin/` – the Pumpkin store, voice chat, components and pages
- `src/routes/pumpkin/` – the `#/pumpkin/<section>` route

`llama-ui.patch` holds the few one-line hooks into upstream files:

| File | Hook |
|---|---|
| `routes/+layout.svelte` | mounts `<PumpkinRoot />` (status chip, voice bar, toasts) |
| `ChatFormActions.svelte` | the voice chat button next to the message box |
| `ChatScreen.svelte` | "Put in chat" from saved prompts |
| `SidebarNavigationActions.svelte`, `ui.constants.ts` | the Pumpkin entry in the sidebar |
| (build step) | `build.sh` adds `qrcode-generator` for the phone QR code |

`LLAMA_TAG` pins the llama.cpp release the UI is built from. Pumpkin downloads the same release of
llama-server by default, so the UI and the server always match.

## Build

```sh
./build.sh            # needs git and Node.js 20+; writes ../pumpkin/ui
```

## Moving to a newer llama.cpp

1. Put the new tag in `LLAMA_TAG` and run `./build.sh`.
2. If the patch no longer applies, fix the hooks in `.build/llama.cpp/tools/ui/src`, then save them with
   `git -C .build/llama.cpp diff -- tools/ui/src tools/ui/package.json > llama-ui.patch`.

Pumpkin's pages render only when the page is served by the Pumpkin server (they check
`/pumpkin/api/state`), so a build of this UI behaves like stock llama.cpp anywhere else.
