<script lang="ts">
	import { LoaderCircle, Zap } from '@lucide/svelte';
	import { Button } from '$lib/components/ui/button';
	import Card from './Card.svelte';
	import Progress from './Progress.svelte';
	import { api, pumpkin } from '../store.svelte';
	import type { Job, Preset, SpecResult } from '../types';
	import { onDestroy, onMount } from 'svelte';

	let job = $state<Job | null>(null);
	let last = $state<{ preset: string; result: SpecResult } | null>(null);
	let timer: ReturnType<typeof setInterval> | undefined;

	const running = $derived(pumpkin.state?.server.state === 'ready' ? (pumpkin.state.server.preset?.id ?? null) : null);

	async function loadLast(id: string | null) {
		if (!id || job?.state === 'running') return;

		const full = (await api<{ items: Preset[] }>('presets').catch(() => null))?.items.find((x) => x.id === id);

		last = full?.spec_result ? { preset: full.name, result: full.spec_result } : null;
	}

	// the measurement shown is the running preset's (asked again only when another preset runs)
	$effect(() => {
		void loadLast(running);
	});

	onMount(async () => {
		const j = (await api<{ job: Job | null }>('jobs?kind=spec').catch(() => null))?.job;

		if (j?.state === 'running') follow(j);
	});
	onDestroy(() => clearInterval(timer));

	function follow(j: Job) {
		job = j;
		clearInterval(timer);
		timer = setInterval(async () => {
			job = await api<Job>(`jobs/${job!.id}`).catch(() => job);

			if (job && job.state !== 'running') {
				clearInterval(timer);

				if (job.state === 'done') {
					const r = job.result as SpecResult;

					last = { preset: job.preset ?? '', result: r };
				}
			}
		}, 1500);
	}

	async function measure() {
		const j = await pumpkin.run(() => api<Job>('bench/spec', { body: {} }));

		if (j) follow(j);
	}

	const row = (r: SpecResult['on']) => `${r.new} t/s new text, ${r.edit} t/s rewriting`;
</script>

<Card
	description="Faster replies guesses the next words from the chat and checks them in one step. It helps most when the answer repeats text, and on some models it costs speed. This runs the speed test with it on and off for the running preset (the model restarts twice), then puts everything back."
	title="Faster replies"
>
	{#snippet actions()}
		<Button class="rounded-full" disabled={job?.state === 'running' || !running} onclick={measure} size="sm">
			{#if job?.state === 'running'}<LoaderCircle class="h-4 w-4 animate-spin" /> Measuring{:else}<Zap class="h-4 w-4" /> Measure{/if}
		</Button>
	{/snippet}

	{#if job?.state === 'running'}
		<Progress done={job.done} label={job.phase ?? 'Measuring'} total={job.total} />
	{:else if job?.state === 'error'}
		<p class="text-sm text-destructive">{job.error}</p>
	{/if}

	{#if last}
		<div class={['text-sm', job?.state === 'running' && 'mt-3']}>
			<p class="font-medium">{last.preset}: {last.result.text}</p>
			<dl class="mt-2 grid gap-1 text-xs text-muted-foreground sm:grid-cols-2">
				<div><dt class="inline font-medium text-foreground">On:</dt> <dd class="inline">{row(last.result.on)}{last.result.on.accepted != null ? `, ${last.result.on.accepted}% of guesses right` : ''}</dd></div>
				<div><dt class="inline font-medium text-foreground">Off:</dt> <dd class="inline">{row(last.result.off)}</dd></div>
			</dl>
			<p class="mt-2 text-xs text-muted-foreground">Turn it on or off per preset: Presets > Edit > Faster replies.</p>
		</div>
	{:else if !running && job?.state !== 'running'}
		<p class="text-sm text-muted-foreground">Start a preset to measure it.</p>
	{/if}
</Card>
