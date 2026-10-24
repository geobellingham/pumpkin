<script lang="ts">
	import { Bot, Brain, Clock, Download, Eye, Hourglass, Image, LoaderCircle, SquareTerminal } from '@lucide/svelte';
	import { onMount } from 'svelte';
	import { pumpkin } from '../store.svelte';
	import type { ActivityItem } from '../types';

	let { compact = false, kinds }: { compact?: boolean; kinds?: ActivityItem['kind'][] } = $props();

	let tick = $state(Date.now());

	onMount(() => {
		const t = setInterval(() => (tick = Date.now()), 1000);

		return () => clearInterval(t);
	});

	const items = $derived((pumpkin.state?.activity ?? []).filter((a) => !kinds || kinds.includes(a.kind)));

	const ICON = { agent: Clock, code: SquareTerminal, download: Download, image: Image, job: Hourglass, model: Brain, vision: Eye };

	function elapsed(a: ActivityItem): string {
		if (!a.since || a.state === 'idle') return '';

		const s = Math.max(0, Math.round(tick / 1000 + pumpkin.skew - a.since));

		return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, '0')}s`;
	}

	function tokens(n: number): string {
		return n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : String(n);
	}
</script>

{#snippet dot(a: ActivityItem)}
	<span
		aria-hidden="true"
		class={[
			'inline-block size-2 shrink-0 rounded-full',
			a.state === 'working' && 'animate-pulse bg-orange-500',
			a.state === 'loading' && 'animate-pulse bg-amber-500',
			a.state === 'waiting' && 'bg-sky-500',
			a.state === 'paused' && 'bg-amber-500',
			a.state === 'idle' && 'bg-emerald-500'
		]}
	></span>
{/snippet}

{#if compact}
	<div aria-live="polite" class="flex min-w-0 flex-wrap gap-1.5">
		{#each items.filter((a) => a.state !== 'idle') as a (a.id)}
			<span class="inline-flex max-w-full min-w-0 items-center gap-1.5 rounded-full bg-muted px-2.5 py-1 text-xs">
				{@render dot(a)}
				<span class="truncate"><span class="font-medium">{a.title}</span> · {a.label}{elapsed(a) ? ` · ${elapsed(a)}` : ''}</span>
			</span>
		{/each}
	</div>
{:else}
	<ul aria-live="polite" class="flex flex-col divide-y divide-border/60">
		{#each items as a (a.id)}
			{@const Icon = ICON[a.kind] ?? Bot}
			<li class="flex items-start gap-3 py-3 first:pt-0 last:pb-0">
				<span class="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-lg bg-muted">
					{#if a.state === 'loading'}
						<LoaderCircle class="size-4 animate-spin text-muted-foreground" />
					{:else}
						<Icon class="size-4 text-muted-foreground" />
					{/if}
				</span>

				<div class="min-w-0 flex-1">
					<div class="flex flex-wrap items-center gap-x-2 gap-y-0.5">
						<span class="truncate text-sm font-medium">{a.title}</span>
						<span class="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
							{@render dot(a)}
							{a.label}
						</span>
						{#if elapsed(a)}<span class="text-xs text-muted-foreground tabular-nums">{elapsed(a)}</span>{/if}
						{#if a.detail}<span class="text-xs text-muted-foreground tabular-nums">· {a.detail}</span>{/if}
					</div>

					{#if a.progress != null}
						<div class="mt-1.5 h-1.5 overflow-hidden rounded-full bg-foreground/10">
							<div class="h-full rounded-full bg-orange-500 transition-[width] duration-500" style="width: {Math.round(a.progress * 100)}%"></div>
						</div>
					{/if}

					{#if a.context}
						{@const pct = Math.min(100, (a.context.used / a.context.total) * 100)}
						<div class="mt-1.5 flex items-center gap-2 text-xs text-muted-foreground">
							<span class="shrink-0">Memory of the chat</span>
							<div class="h-1.5 min-w-12 flex-1 overflow-hidden rounded-full bg-foreground/10">
								<div class={['h-full rounded-full transition-[width] duration-500', pct > 80 ? 'bg-amber-500' : 'bg-foreground/70']} style="width: {pct}%"></div>
							</div>
							<span class="shrink-0 tabular-nums">{tokens(a.context.used)} of {tokens(a.context.total)}</span>
						</div>
					{/if}

					{#if a.note}<p class="mt-1 text-xs text-muted-foreground">{a.note}</p>{/if}
				</div>
			</li>
		{:else}
			<li class="py-1 text-sm text-muted-foreground">Nothing is running. Start a model below and its work shows up here.</li>
		{/each}
	</ul>
{/if}
