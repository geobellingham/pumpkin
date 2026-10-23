<script lang="ts">
	// The picture the chat's model drew with image_generate, shown in the chat itself (other tool calls fold away).
	// Pumpkin's tool answer carries the picture as a data URI line; the chat moved it into the message's extras and
	// left "[Attachment saved: name]" in its place.
	import { Image as ImageIcon, LoaderCircle } from '@lucide/svelte';
	import { ATTACHMENT_SAVED_REGEX } from '$lib/constants/agentic.constants';
	import { AttachmentType } from '$lib/enums';
	import type { AgenticSection, DatabaseMessageExtraImageFile } from '$lib/types';

	let { section }: { isStreaming?: boolean; section: AgenticSection } = $props();

	const prompt = $derived.by(() => {
		try {
			return String(JSON.parse(section.toolArgs || '{}').prompt || '');
		} catch {
			return '';
		}
	});

	const picture = $derived.by(() => {
		const name = section.toolResult?.match(ATTACHMENT_SAVED_REGEX)?.[1];

		if (!name) return null;

		return (
			(section.toolResultExtras ?? []).find(
				(e): e is DatabaseMessageExtraImageFile => e.type === AttachmentType.IMAGE && e.name === name
			) ?? null
		);
	});

	const saved = $derived(section.toolResult?.match(/saved as (\S+) in their Files/)?.[1] ?? '');
	const failed = $derived(!!section.toolResult && !picture);

	function open() {
		if (!picture) return;

		const w = window.open();

		if (w) w.document.write(`<img src="${picture.base64Url}" style="max-width:100%">`);
	}
</script>

<figure class="my-2">
	{#if picture}
		<button class="block cursor-zoom-in" onclick={open} title="Open the full picture" type="button">
			<img alt={prompt || 'Picture'} class="max-h-[70vh] max-w-full rounded-xl border border-border/60 object-contain" src={picture.base64Url} />
		</button>
		<figcaption class="mt-1 text-xs text-muted-foreground">
			{saved ? `Saved in Files as ${saved}` : 'Saved in Files'}
		</figcaption>
	{:else if failed}
		<div class="flex items-start gap-2 rounded-xl border border-border/60 p-3 text-sm text-muted-foreground">
			<ImageIcon class="mt-0.5 h-4 w-4 shrink-0" />
			<span>No picture: {section.toolResult?.replace(/^Error:\s*/, '')}</span>
		</div>
	{:else}
		<div class="flex aspect-square w-full max-w-sm flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-border p-4 text-center text-sm text-muted-foreground">
			<LoaderCircle class="h-5 w-5 animate-spin" />
			<span>Picture{prompt ? `: ${prompt.slice(0, 120)}` : ''}</span>
			<span class="text-xs">While it is drawn the chat model may pause, and it comes back by itself.</span>
		</div>
	{/if}
</figure>
