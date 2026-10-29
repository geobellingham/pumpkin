<script lang="ts">
	// The panel next to the chat: it opens by itself when the model makes or changes an artifact, and stays out of the
	// way otherwise. Only on the chat, never on Pumpkin's own pages (they have an Artifacts page).
	import { page } from '$app/state';
	import { Layers, X } from '@lucide/svelte';
	import { Button } from '$lib/components/ui/button';
	import { api, pumpkin } from '../store.svelte';
	import ArtifactView from './ArtifactView.svelte';
	import { onDestroy, onMount } from 'svelte';
	import { fly } from 'svelte/transition';

	interface Item {
		id: string;
		title: string;
		updated: number;
	}

	let items = $state<Item[]>([]);
	let enabled = $state(false);
	let open = $state(false);
	let current = $state<string | null>(null);
	let seen = -1;                                                  // newest change we have shown (-1: first look)
	let timer: ReturnType<typeof setInterval> | undefined;

	const onChat = $derived(!(page.route.id?.startsWith('/pumpkin') ?? false));
	const phone = $derived(pumpkin.state?.platform === 'android');

	async function poll() {
		if (document.visibilityState !== 'visible') return;

		const r = await api<{ enabled: boolean; items: Item[] }>('artifacts').catch(() => null);

		if (!r) return;

		enabled = r.enabled;
		items = r.items;

		const newest = r.items[0];

		if (!newest) return;

		if (seen < 0) {
			seen = newest.updated;                                  // what was there before this page opened stays closed
			current = current ?? newest.id;

			return;
		}

		if (newest.updated > seen) {
			seen = newest.updated;
			current = newest.id;
			open = true;                                            // the model just made or changed something
		}
	}

	onMount(() => {
		void poll();
		timer = setInterval(poll, 2500);
	});

	onDestroy(() => clearInterval(timer));

	// make room: the chat gets narrower instead of sitting under the panel
	$effect(() => {
		const show = open && onChat && enabled && !phone;

		document.documentElement.classList.toggle('pumpkin-dock-open', show);
	});

	$effect(() => {
		if (current && !items.some((i) => i.id === current)) current = items[0]?.id ?? null;
	});
</script>

{#if onChat && enabled && items.length}
	{#if !open}
		<button
			aria-label="Open artifacts"
			class="fixed right-3 bottom-24 z-30 flex items-center gap-1.5 rounded-full border border-border bg-background px-3 py-2 text-xs shadow-md hover:bg-muted"
			onclick={() => (open = true)}
		>
			<Layers class="h-4 w-4" />Artifacts
		</button>
	{:else}
		<aside
			class="fixed top-0 right-0 z-40 flex h-dvh w-full flex-col gap-2 border-l border-border bg-background p-3 shadow-xl md:w-[var(--pumpkin-dock-w)]"
			transition:fly={{ duration: 160, x: 40 }}
		>
			<div class="flex items-center gap-2">
				<select
					aria-label="Artifact"
					bind:value={current}
					class="h-8 min-w-0 flex-1 rounded-lg border border-input bg-transparent px-2 text-sm dark:bg-input/30"
				>
					{#each items as it (it.id)}
						<option class="bg-popover text-popover-foreground" value={it.id}>{it.title}</option>
					{/each}
				</select>
				<Button aria-label="Close" class="size-8 shrink-0 rounded-full" onclick={() => (open = false)} size="icon" variant="ghost"><X class="h-4 w-4" /></Button>
			</div>

			{#if current}
				<ArtifactView id={current} ondeleted={() => (current = null)} />
			{/if}
		</aside>
	{/if}
{/if}

<style>
	:global(:root) {
		--pumpkin-dock-w: min(46vw, 760px);
	}

	@media (min-width: 768px) {
		:global(html.pumpkin-dock-open body) {
			padding-right: var(--pumpkin-dock-w);
		}
	}
</style>
