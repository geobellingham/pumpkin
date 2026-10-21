<script lang="ts">
	import { BadgeCheck, Eye, LoaderCircle, RefreshCw, Sparkles, Wrench } from '@lucide/svelte';
	import { Button } from '$lib/components/ui/button';
	import Card from '../components/Card.svelte';
	import FitBadge from '../components/FitBadge.svelte';
	import { api, pumpkin } from '../store.svelte';
	import type { Fit } from '../types';
	import { onMount } from 'svelte';

	interface Rec {
		active_b: number | null;
		blurb: string;
		downloads: number;
		fit: Fit;
		id: string;
		installed: boolean;
		moe: boolean;
		name: string;
		org: string;
		params_b: number;
		quant: string;
		ram_share?: number;
		repo: string;
		route: 'gpu' | 'moe';
		size_gb: number;
		tested?: boolean;
		tools: boolean;
		vision: boolean;
	}

	interface Recs {
		error: string | null;
		gpu: Rec[];
		hardware: { phone?: boolean; ram_total: number; unified?: boolean; vram_total: number };
		moe: Rec[];
		source: 'huggingface' | 'offline';
		stale: boolean;
		updated: number | null;
	}

	let { goal, onhardware, ontop }: { goal?: string; onhardware?: (hw: Recs['hardware']) => void; ontop?: (r: Recs) => void } = $props();

	let recs = $state<Recs | null>(null);
	let refreshing = $state(false);
	let expanded = $state<Record<string, boolean>>({});

	async function load(refresh = false) {
		refreshing = refresh;

		const r = await pumpkin.run(() => api<Recs>(`recommend?${refresh ? 'refresh=1&' : ''}${goal ? `goal=${goal}` : ''}`));

		refreshing = false;

		if (r) {
			recs = r;
			onhardware?.(r.hardware);
			ontop?.(r);
		}
	}

	onMount(() => load());

	async function install(m: Rec) {
		const r = await pumpkin.run(
			() => api(`catalog/${encodeURIComponent(m.id)}`, { body: { start: true } }),
			`Installing ${m.name}`
		);

		if (r) location.hash = '#/pumpkin/overview';
	}

	function ago(ts: number | null) {
		if (!ts) return '';

		const h = (Date.now() / 1000 - ts) / 3600;

		return h < 1 ? 'just now' : h < 36 ? `${Math.round(h)} h ago` : `${Math.round(h / 24)} days ago`;
	}

	const fmt = (n: number) => (n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${Math.round(n / 1e3)}k` : `${n}`);
</script>

{#snippet card(m: Rec)}
	{@const installing = pumpkin.state?.installs.find((i) => i.model === m.name && i.state === 'running')}
	<div class="flex flex-col gap-2 rounded-xl border border-border/60 p-3.5">
		<div class="flex items-start justify-between gap-2">
			<div class="min-w-0">
				<div class="truncate font-medium" title={m.repo}>{m.name}</div>
				<div class="truncate text-xs text-muted-foreground">
					{m.org}{m.downloads ? ` · ${fmt(m.downloads)} downloads` : ''}
				</div>
			</div>
			<div class="flex shrink-0 items-center gap-1.5 text-muted-foreground">
				{#if m.tested}<span title="Tested with Pumpkin"><BadgeCheck class="h-4 w-4 text-emerald-500" /></span>{/if}
				{#if m.tools}<span title="Can use tools"><Wrench class="h-3.5 w-3.5" /></span>{/if}
				{#if m.vision}<span title="Understands images"><Eye class="h-4 w-4" /></span>{/if}
			</div>
		</div>

		<p class="text-sm leading-snug text-muted-foreground">{m.blurb}</p>

		<div class="mt-auto flex items-center justify-between gap-2 pt-1">
			<div class="flex min-w-0 flex-col gap-1">
				<FitBadge fit={m.fit} short />
				<span class="text-xs text-muted-foreground">{m.quant} · about {m.size_gb} GB</span>
			</div>

			{#if m.installed}
				<span class="text-xs font-medium text-emerald-600 dark:text-emerald-400">Installed</span>
			{:else if installing}
				<span class="flex items-center gap-1.5 text-xs"
					><LoaderCircle class="h-3.5 w-3.5 animate-spin" />{installing.total
						? Math.round((installing.done / installing.total) * 100) + '%'
						: 'Starting'}</span
				>
			{:else}
				<Button class="rounded-full" onclick={() => install(m)} size="sm">
					<Sparkles class="h-3.5 w-3.5" /> Install
				</Button>
			{/if}
		</div>
	</div>
{/snippet}

{#snippet route(key: 'gpu' | 'moe', title: string, description: string, empty: string)}
	{@const list = recs?.[key] ?? []}
	{@const shown = expanded[key] ? list : list.slice(0, 4)}
	<Card {description} {title}>
		{#if recs === null}
			<div class="flex justify-center py-8"><LoaderCircle class="h-5 w-5 animate-spin text-muted-foreground" /></div>
		{:else if !list.length}
			<p class="text-sm text-muted-foreground">{empty}</p>
		{:else}
			<div class="grid gap-3 sm:grid-cols-2">
				{#each shown as m (m.id)}{@render card(m)}{/each}
			</div>
			{#if list.length > 4}
				<button
					class="mt-3 text-sm font-medium text-muted-foreground hover:text-foreground"
					onclick={() => (expanded[key] = !expanded[key])}
					type="button"
				>
					{expanded[key] ? 'Show fewer' : `Show ${list.length - 4} more`}
				</button>
			{/if}
		{/if}
	</Card>
{/snippet}

<div class="flex flex-col gap-4">
	<div class="flex items-center justify-between gap-3 px-1">
		<p class="text-sm text-muted-foreground">
			{#if recs?.source === 'offline'}
				Showing tested picks. Hugging Face could not be reached, so newer models are missing.
			{:else}
				Picked for your computer from the most popular and trending models{recs?.updated ? `, updated ${ago(recs.updated)}` : ''}.
			{/if}
		</p>
		<Button aria-label="Check for new models" class="size-9 shrink-0 rounded-full" disabled={refreshing} onclick={() => load(true)} size="icon" variant="ghost">
			<RefreshCw class={['h-4 w-4', refreshing && 'animate-spin']} />
		</Button>
	</div>

	{@render route(
		'gpu',
		recs?.hardware.phone ? 'Fits your phone' : 'Runs fully on your GPU',
		recs?.hardware.phone ? 'Chosen for the memory your phone can spare.' : recs?.hardware.unified ? 'The whole model fits in the memory your GPU can use.' : 'The fastest option: the whole model sits in video memory.',
		'Nothing fits entirely in your video memory. Try the models below, which can use RAM too.'
	)}

	{#if recs?.hardware.unified}
		<p class="px-1 text-sm text-muted-foreground">
			{#if recs?.hardware.phone}
				On a phone, models run on the processor and share its memory, so the list above has the ones that fit. Smaller models
				answer faster.
			{:else}
				On a Mac the GPU and the CPU share one pool of memory, so every model that fits is in the list above. Mixture-of-experts
				models are a great pick here: they are big but only use a few billion parameters for each word, so they stay fast.
			{/if}
		</p>
	{:else}
	{@render route(
		'moe',
		'Bigger models that also use RAM',
		'Mixture-of-experts models only use a few billion parameters for each word, so they stay quick with part of the model in RAM. Smarter, a little slower.',
		'No mixture-of-experts model fits this computer well right now.'
	)}
	{/if}
</div>
