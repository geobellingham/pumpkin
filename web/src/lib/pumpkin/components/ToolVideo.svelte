<script lang="ts">
	// The clip the chat's model made with image_video, played in the chat itself. Pumpkin saved it in the user's
	// Files and its tool answer names the file; the clip streams from there.
	import { Clapperboard, LoaderCircle } from '@lucide/svelte';
	import type { AgenticSection } from '$lib/types';

	let { section }: { isStreaming?: boolean; section: AgenticSection } = $props();

	const args = $derived.by(() => {
		try {
			return JSON.parse(section.toolArgs || '{}') as { prompt?: string; seconds?: number };
		} catch {
			return {};
		}
	});

	const saved = $derived(section.toolResult?.match(/saved as (\S+) in their Files/)?.[1] ?? '');
	const src = $derived(saved ? `./pumpkin/api/files/download?path=${encodeURIComponent(saved)}&inline=1` : '');
	const animated = $derived(saved.toLowerCase().endsWith('.webp'));
	const failed = $derived(!!section.toolResult && !saved);
	let started = $state(Date.now());
	let now = $state(Date.now());

	$effect(() => {
		if (section.toolResult) return;

		started = Date.now();
		const t = setInterval(() => (now = Date.now()), 1000);

		return () => clearInterval(t);
	});

	const waited = $derived(Math.max(0, Math.round((now - started) / 1000)));
</script>

<figure class="my-2">
	{#if src}
		{#if animated}
			<img alt={args.prompt || 'Video'} class="max-h-[70vh] max-w-full rounded-xl border border-border/60 object-contain" {src} />
		{:else}
			<!-- svelte-ignore a11y_media_has_caption -->
			<video class="max-h-[70vh] max-w-full rounded-xl border border-border/60" controls loop playsinline preload="metadata" {src}></video>
		{/if}
		<figcaption class="mt-1 text-xs text-muted-foreground">
			Saved in Files as {saved}. <a class="underline" download href={src}>Download</a>
		</figcaption>
	{:else if failed}
		<div class="flex items-start gap-2 rounded-xl border border-border/60 p-3 text-sm text-muted-foreground">
			<Clapperboard class="mt-0.5 h-4 w-4 shrink-0" />
			<span>No video: {section.toolResult?.replace(/^Error:\s*/, '')}</span>
		</div>
	{:else}
		<div class="flex aspect-video w-full max-w-md flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-border p-4 text-center text-sm text-muted-foreground">
			<LoaderCircle class="h-5 w-5 animate-spin" />
			<span>Video{args.prompt ? `: ${args.prompt.slice(0, 120)}` : ''}</span>
			<span class="text-xs">This takes a few minutes ({Math.floor(waited / 60)}:{String(waited % 60).padStart(2, '0')} so far). The chat model may pause meanwhile and comes back by itself.</span>
		</div>
	{/if}
</figure>
