<script lang="ts">
	// Shown on the chat's full-screen preview of a page or drawing the model wrote: keep it as a file.
	// The preview itself stays in its sandboxed frame; nothing here runs the page on Pumpkin's own address.
	import { Download, FolderDown } from '@lucide/svelte';
	import { api, pumpkin } from '../store.svelte';

	let { code, language }: { code: string; language: string } = $props();

	const kind = $derived(String(language).toLowerCase() === 'svg' ? 'svg' : 'html');
	let saving = $state(false);

	function title(): string {
		const m = code.match(/<title[^>]*>([^<]{1,80})<\/title>/i);

		return m ? m[1].trim() : '';
	}

	async function save() {
		saving = true;

		const r = await pumpkin.run(() => api<{ path: string }>('files/artifact', { body: { content: code, language: kind, name: title() } }));

		saving = false;

		if (r) pumpkin.say(`Saved to Files: ${r.path}`);
	}

	function download() {
		const url = URL.createObjectURL(new Blob([code], { type: kind === 'svg' ? 'image/svg+xml' : 'text/html' }));
		const a = document.createElement('a');

		a.href = url;
		a.download = `${title() || (kind === 'svg' ? 'drawing' : 'page')}.${kind}`;
		a.click();
		setTimeout(() => URL.revokeObjectURL(url), 2000);
	}
</script>

<div class="pumpkin-preview-bar absolute top-4 right-16 z-[100002] flex gap-2">
	{#if pumpkin.available}
		<button class="flex h-9 items-center gap-1.5 rounded-full bg-black/70 px-3.5 text-xs font-medium text-white backdrop-blur hover:bg-black/85 disabled:opacity-60" disabled={saving} onclick={save} type="button">
			<FolderDown class="size-4" /> Save to Files
		</button>
	{/if}
	<button aria-label="Download" class="flex size-9 items-center justify-center rounded-full bg-black/70 text-white backdrop-blur hover:bg-black/85" onclick={download} title="Download" type="button">
		<Download class="size-4" />
	</button>
</div>
