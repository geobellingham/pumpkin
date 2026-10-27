<script lang="ts">
	import { LoaderCircle, Play } from '@lucide/svelte';
	import { MarkdownContent } from '$lib/components/app';
	import { Button } from '$lib/components/ui/button';
	import Card from '../components/Card.svelte';
	import Empty from '../components/Empty.svelte';
	import Progress from '../components/Progress.svelte';
	import { api, pumpkin } from '../store.svelte';
	import type { CompareRow, Job, Preset } from '../types';
	import { onDestroy, onMount } from 'svelte';

	let presets = $state<Preset[] | null>(null);
	let picked = $state<string[]>([]);
	let prompt = $state('');
	let job = $state<Job | null>(null);
	let timer: ReturnType<typeof setInterval> | undefined;

	onMount(async () => {
		presets = (await pumpkin.run(() => api<{ items: Preset[] }>('presets')))?.items ?? [];

		const j = (await api<{ job: Job | null }>('jobs?kind=compare').catch(() => null))?.job;

		if (j) {
			prompt = j.prompt ?? '';
			picked = (j.results ?? []).map((r) => r.id);

			if (j.state === 'running') follow(j);
			else job = j;
		}
	});
	onDestroy(() => clearInterval(timer));

	function follow(j: Job) {
		job = j;
		clearInterval(timer);
		timer = setInterval(async () => {
			job = await api<Job>(`jobs/${job!.id}`).catch(() => job);

			if (job && job.state !== 'running') clearInterval(timer);
		}, 1500);
	}

	function toggle(id: string, on: boolean) {
		picked = on ? [...picked, id].slice(-4) : picked.filter((x) => x !== id);
	}

	async function run() {
		const j = await pumpkin.run(() => api<Job>('compare', { body: { presets: picked, prompt } }));

		if (j) follow(j);
	}

	const rows = $derived<CompareRow[]>(job?.results ?? []);
	const fastest = $derived(Math.max(0, ...rows.map((r) => r.gen ?? 0)));
	const report = $derived((job?.result as { report?: string } | undefined)?.report);
</script>

<div class="flex flex-col gap-4">
	<Card
		description="Ask two to four presets the same question and read the answers side by side. They load one after another, so this takes a while with big models. The preset that was running starts again at the end."
		title="Compare models"
	>
		{#if presets === null}
			<div class="flex justify-center py-6"><LoaderCircle class="h-5 w-5 animate-spin text-muted-foreground" /></div>
		{:else if presets.length < 2}
			<Empty text="Make at least two presets to compare them.">
				<Button class="rounded-full" href="#/pumpkin/presets" size="sm">Presets</Button>
			</Empty>
		{:else}
			<div class="flex flex-wrap gap-x-4 gap-y-2">
				{#each presets as p (p.id)}
					<label class="flex items-center gap-2 text-sm">
						<input checked={picked.includes(p.id)} class="size-4 accent-foreground" onchange={(e) => toggle(p.id, (e.currentTarget as HTMLInputElement).checked)} type="checkbox" />
						{p.name}
					</label>
				{/each}
			</div>
			<textarea
				bind:value={prompt}
				class="mt-3 min-h-24 w-full rounded-xl border border-input bg-transparent p-3 text-sm dark:bg-input/30"
				placeholder="The question to ask each of them, for example: Explain how a heat pump works to a 12-year-old."
			></textarea>
			<div class="mt-3 flex flex-wrap items-center gap-3">
				<Button class="rounded-full" disabled={job?.state === 'running' || picked.length < 2 || !prompt.trim()} onclick={run}>
					{#if job?.state === 'running'}<LoaderCircle class="h-4 w-4 animate-spin" />{:else}<Play class="h-4 w-4" />{/if}
					Compare {picked.length >= 2 ? picked.length : ''}
				</Button>
				{#if picked.length < 2}<span class="text-xs text-muted-foreground">Pick at least two.</span>{/if}
			</div>
			{#if job?.state === 'running'}
				<div class="mt-3"><Progress done={job.done} label={job.phase ?? 'Comparing'} total={job.total} /></div>
			{:else if job?.state === 'error'}
				<p class="mt-3 text-sm text-destructive">{job.error}</p>
			{/if}
		{/if}
	</Card>

	{#if rows.length}
		<div class="grid gap-4 md:grid-cols-2">
			{#each rows as r (r.id)}
				<Card title={r.name}>
					{#if r.error}
						<p class="text-sm text-destructive">{r.error}</p>
					{:else}
						<dl class="mb-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
							<div><dt class="inline">Writing</dt> <dd class={['inline font-medium tabular-nums', r.gen === fastest && rows.length > 1 ? 'text-emerald-600 dark:text-emerald-400' : 'text-foreground']}>{r.gen} t/s</dd></div>
							<div><dt class="inline">Reading</dt> <dd class="inline font-medium text-foreground tabular-nums">{r.prompt} t/s</dd></div>
							<div><dt class="inline">Answer</dt> <dd class="inline font-medium text-foreground tabular-nums">{r.seconds} s, {r.tokens} tokens</dd></div>
							<div><dt class="inline">Load</dt> <dd class="inline font-medium text-foreground tabular-nums">{r.load} s</dd></div>
						</dl>
						{#if r.thinking}
							<details class="mb-2 text-xs text-muted-foreground"><summary class="cursor-pointer">Thinking</summary><p class="mt-1 whitespace-pre-wrap">{r.thinking}</p></details>
						{/if}
						<div class="text-sm"><MarkdownContent content={r.answer ?? ''} /></div>
					{/if}
				</Card>
			{/each}
		</div>
		{#if report}<p class="text-xs text-muted-foreground">Saved as a task report: {report}</p>{/if}
	{/if}
</div>
