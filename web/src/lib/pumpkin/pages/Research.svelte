<script lang="ts">
	// Research: ask a question, and Pumpkin plans searches, reads the pages, takes notes and writes a report with numbered
	// sources. It takes minutes. Pumpkin does the searching and reading itself (the model only reads and writes), and
	// every source in the report is a page it really opened.
	import { ChevronLeft, Layers, LoaderCircle, Search, Trash } from '@lucide/svelte';
	import { MarkdownContent } from '$lib/components/app';
	import { Button } from '$lib/components/ui/button';
	import { Textarea } from '$lib/components/ui/textarea';
	import Card from '../components/Card.svelte';
	import Empty from '../components/Empty.svelte';
	import Field from '../components/Field.svelte';
	import Segmented from '../components/Segmented.svelte';
	import Select from '../components/Select.svelte';
	import Toggle from '../components/Toggle.svelte';
	import { api, pumpkin } from '../store.svelte';
	import type { Preset } from '../types';
	import { onDestroy, onMount } from 'svelte';

	interface Summary {
		depth: string;
		done: number;
		error: string;
		finished: number | null;
		id: string;
		pages: number;
		phase: string;
		question: string;
		report_name: string;
		started: number;
		state: string;
		total: number;
	}

	interface Full extends Summary {
		log: { t: number; text: string }[];
		plan: { query: string; state: string }[];
		report: string;
		sources: { n: number; title: string; url: string }[];
	}

	let items = $state<Summary[] | null>(null);
	let busy = $state(false);
	let current = $state<Full | null>(null);
	let open = $state<Full | null>(null);
	let presets = $state<Preset[]>([]);
	let question = $state('');
	let depth = $state('normal');
	let preset = $state('');
	let stopAfter = $state(false);
	let timer: ReturnType<typeof setInterval> | undefined;

	const DEPTHS = [
		{ label: 'Quick', value: 'quick' },
		{ label: 'Normal', value: 'normal' },
		{ label: 'Deep', value: 'deep' }
	];
	const HINT: Record<string, string> = {
		deep: 'Up to 8 searches and 24 pages, with two rounds of looking for what is missing. Can take an hour on a slow model.',
		normal: 'Up to 5 searches and 12 pages, with one round of looking for what is missing. Usually 5 to 20 minutes.',
		quick: '3 searches and up to 6 pages. A few minutes.'
	};

	async function poll() {
		const r = await api<{ busy: boolean; current: Summary | null; items: Summary[] }>('research').catch(() => null);

		if (!r) return;

		busy = r.busy;
		items = r.items;

		if (r.current) {
			current = await api<Full>(`research/${r.current.id}`).catch(() => current);
		} else if (current) {
			// it just finished: show the report
			const done = await api<Full>(`research/${current.id}`).catch(() => null);

			current = null;

			if (done && done.state === 'done') open = done;
			else if (done?.state === 'error') pumpkin.say(done.error || 'The research failed.', 'error');
		}
	}

	onMount(async () => {
		const p = await api<{ items: Preset[] }>('presets').catch(() => null);

		presets = p?.items ?? [];
		await poll();
		timer = setInterval(poll, 2000);
	});

	onDestroy(() => clearInterval(timer));

	async function start() {
		const q = question.trim();

		if (q.length < 5) return;

		const r = await pumpkin.run(() => api('research', { body: { depth, preset: preset || null, question: q, stop_after: stopAfter } }));

		if (r) {
			question = '';
			open = null;
			await poll();
		}
	}

	async function show(s: Summary) {
		open = await pumpkin.run(() => api<Full>(`research/${s.id}`));
	}

	async function remove(s: Summary) {
		if (!confirm('Delete this research? Its report stays on the Reports page.')) return;

		if (await pumpkin.run(() => api(`research/${s.id}`, { method: 'DELETE' }))) {
			if (open?.id === s.id) open = null;

			await poll();
		}
	}

	async function keep() {
		if (!open) return;

		const r = await pumpkin.run(() =>
			api('artifacts', { body: { content: open!.report, kind: 'md', title: open!.question.slice(0, 80) } }), 'Kept as an artifact');

		if (r) location.hash = '#/pumpkin/artifacts';
	}

	const when = (t: number) => new Date(t * 1000).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
	const STATE: Record<string, string> = { cancelled: 'Stopped', done: 'Done', error: 'Failed', planning: 'Working', reading: 'Working', writing: 'Working' };
	const pct = $derived(current && current.total ? Math.min(99, Math.round((100 * current.done) / current.total)) : 0);
</script>

<div class="flex flex-col gap-4">
	{#if open}
		<Card title={open.question}>
			{#snippet actions()}
				<Button class="rounded-full" onclick={() => (open = null)} size="sm" variant="ghost"><ChevronLeft class="mr-1 h-4 w-4" />Back</Button>
				{#if open?.report}
					<Button class="rounded-full" onclick={keep} size="sm" variant="outline"><Layers class="mr-1 h-4 w-4" />Keep as artifact</Button>
				{/if}
			{/snippet}

			{#if open.error}
				<p class="mb-3 rounded-xl bg-destructive/10 p-3 text-sm text-destructive">{open.error}</p>
			{/if}

			{#if open.report}
				<MarkdownContent content={open.report} />
			{:else}
				<p class="text-sm text-muted-foreground">There is no report: it did not finish.</p>
				{#if open.log?.length}
					<pre class="mt-2 max-h-72 overflow-auto rounded-xl bg-muted/40 p-3 text-xs whitespace-pre-wrap">{open.log.map((l) => l.text).join('\n')}</pre>
				{/if}
			{/if}
		</Card>
	{:else}
		{#if current}
			<Card description={current.phase} title="Researching">
				{#snippet actions()}
					<Button class="rounded-full" onclick={() => pumpkin.run(() => api('research/cancel', { body: {} }))} size="sm" variant="outline">Stop</Button>
				{/snippet}

				<p class="mb-2 text-sm font-medium">{current.question}</p>
				<div class="h-1.5 overflow-hidden rounded-full bg-foreground/10">
					<div class="h-full rounded-full bg-foreground/80 transition-[width] duration-500" style="width:{Math.max(4, pct)}%"></div>
				</div>

				<div class="mt-3 grid gap-3 md:grid-cols-2">
					<div>
						<p class="mb-1 text-xs font-medium text-muted-foreground">Searches</p>
						<ul class="space-y-0.5 text-sm">
							{#each current.plan as s (s.query)}
								<li class="flex gap-2"><span class="text-muted-foreground">{s.state === 'done' ? '✓' : s.state === 'waiting' ? '…' : '–'}</span><span class="break-words">{s.query}</span></li>
							{/each}
						</ul>
					</div>
					<div>
						<p class="mb-1 text-xs font-medium text-muted-foreground">Pages read ({current.sources.length})</p>
						<ul class="space-y-0.5 text-sm">
							{#each current.sources as s (s.n)}
								<li class="truncate">[{s.n}] {s.title || s.url}</li>
							{/each}
						</ul>
					</div>
				</div>

				{#if current.log.length}
					<pre class="mt-3 max-h-40 overflow-auto rounded-xl bg-muted/40 p-3 text-xs whitespace-pre-wrap">{current.log.slice(-12).map((l) => l.text).join('\n')}</pre>
				{/if}
			</Card>
		{:else}
			<Card
				description="Pumpkin searches the web, reads the pages, takes notes and writes a report. Every source in the report is a page it really opened. The model only reads and writes: Pumpkin does the browsing, so a hostile page cannot give it orders."
				title="Research"
			>
				<form
					class="flex flex-col gap-3"
					onsubmit={(e) => {
						e.preventDefault();
						void start();
					}}
				>
					<Textarea bind:value={question} maxlength={1500} placeholder="What do you want to find out? Be specific: “How do heat pumps compare with gas boilers for a 1970s house in northern Europe?”" rows={3} />

					<div class="flex flex-wrap items-end gap-4">
						<Field hint={HINT[depth]} label="How thorough">
							<Segmented bind:value={depth} options={DEPTHS} />
						</Field>
						<Field label="Model">
							<Select bind:value={preset} options={[{ label: 'Whatever is running', value: '' }, ...presets.map((p) => ({ label: p.name, value: p.id }))]} />
						</Field>
					</div>

					<Toggle bind:checked={stopAfter} description="Only when Pumpkin had to start it for this." label="Stop the model afterwards" />

					<div>
						<Button class="rounded-full" disabled={question.trim().length < 5 || busy} type="submit"><Search class="mr-1.5 h-4 w-4" />Start research</Button>
					</div>
				</form>
			</Card>
		{/if}

		{#if items === null}
			<div class="flex justify-center py-8"><LoaderCircle class="h-5 w-5 animate-spin text-muted-foreground" /></div>
		{:else if !items.length}
			<Empty text="Nothing yet. Your research is kept here, and its reports on the Reports page." />
		{:else}
			<Card title="Earlier research">
				<ul class="flex flex-col divide-y divide-border/60">
					{#each items.filter((i) => !current || i.id !== current.id) as it (it.id)}
						<li class="flex items-center gap-2 py-2">
							<button class="min-w-0 flex-1 text-left" onclick={() => show(it)}>
								<span class="block truncate text-sm">{it.question}</span>
								<span class="text-xs text-muted-foreground">{STATE[it.state] ?? it.state} · {it.pages} pages · {when(it.started)}</span>
							</button>
							<Button aria-label="Delete" class="size-9 shrink-0 rounded-full" onclick={() => remove(it)} size="icon" variant="ghost"><Trash class="h-4 w-4" /></Button>
						</li>
					{/each}
				</ul>
			</Card>
		{/if}
	{/if}
</div>
