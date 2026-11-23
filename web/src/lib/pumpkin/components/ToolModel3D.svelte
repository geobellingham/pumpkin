<script lang="ts">
	// The 3D model the chat's model made with image_3d, shown in the chat itself. Pumpkin saved it in the user's
	// Files and its tool answer names the file; the viewer loads it from there.
	import { Box, LoaderCircle } from '@lucide/svelte';
	import type { AgenticSection } from '$lib/types';
	import ModelViewer from './ModelViewer.svelte';

	let { section }: { isStreaming?: boolean; section: AgenticSection } = $props();

	const args = $derived.by(() => {
		try {
			return JSON.parse(section.toolArgs || '{}') as { from_picture?: boolean; prompt?: string };
		} catch {
			return {};
		}
	});

	const saved = $derived(section.toolResult?.match(/saved as (\S+) in their Files/)?.[1] ?? '');
	const src = $derived(saved ? `./pumpkin/api/files/download?path=${encodeURIComponent(saved)}&inline=1` : '');
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
		<ModelViewer {src} />
		<figcaption class="mt-1 text-xs text-muted-foreground">
			Drag to turn it, scroll to zoom. Saved in Files as {saved}. <a class="underline" download href={src}>Download</a>
		</figcaption>
	{:else if failed}
		<div class="flex items-start gap-2 rounded-xl border border-border/60 p-3 text-sm text-muted-foreground">
			<Box class="mt-0.5 h-4 w-4 shrink-0" />
			<span>No 3D model: {section.toolResult?.replace(/^Error:\s*/, '')}</span>
		</div>
	{:else}
		<div class="flex aspect-square w-full max-w-md flex-col items-center justify-center gap-2 pk-option rounded-[16px] p-4 text-center text-sm text-muted-foreground">
			<LoaderCircle class="h-5 w-5 animate-spin" />
			<span>3D model{args.prompt ? `: ${args.prompt.slice(0, 120)}` : args.from_picture ? ' from your picture' : ''}</span>
			<span class="text-xs">This takes several minutes ({Math.floor(waited / 60)}:{String(waited % 60).padStart(2, '0')} so far). The chat model may pause meanwhile and comes back by itself.</span>
		</div>
	{/if}
</figure>
