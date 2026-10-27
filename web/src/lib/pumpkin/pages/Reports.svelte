<script lang="ts">
	import { ChevronLeft, FileText, Gauge, LoaderCircle, Trash } from '@lucide/svelte';
	import { MarkdownContent } from '$lib/components/app';
	import { Button } from '$lib/components/ui/button';
	import Card from '../components/Card.svelte';
	import Empty from '../components/Empty.svelte';
	import Segmented from '../components/Segmented.svelte';
	import SpecCard from '../components/SpecCard.svelte';
	import Compare from './Compare.svelte';
	import { api, pumpkin } from '../store.svelte';
	import { onDestroy, onMount } from 'svelte';

	interface File {
		mtime: number;
		name: string;
	}
	interface Row {
		gen: string;
		model: string;
		prompt: string;
		time: string;
		vram: string;
	}

	let tab = $state('reports');
	let items = $state<File[] | null>(null);
	let dir = $state('');
	let open = $state<{ name: string; text: string } | null>(null);
	let bench = $state<{ error: string | null; history: Row[]; running: boolean } | null>(null);
	let timer: ReturnType<typeof setInterval> | undefined;

	async function loadList() {
		items = null;

		const r = await pumpkin.run(() => api<{ dir: string; items: File[] }>(`reports${tab === 'bench-files' ? '?kind=bench' : ''}`));

		items = r?.items ?? [];
		dir = r?.dir ?? '';
	}

	async function loadBench() {
		bench = await api<typeof bench>('bench').catch(() => null);
	}

	onMount(() => {
		void loadList();
		void loadBench();
		timer = setInterval(() => bench?.running && loadBench(), 1500);
	});

	onDestroy(() => clearInterval(timer));

	async function show(name: string) {
		const r = await pumpkin.run(() =>
			api<{ name: string; text: string }>(`reports?name=${encodeURIComponent(name)}${tab === 'bench-files' ? '&kind=bench' : ''}`)
		);

		if (r) open = r;
	}

	async function remove(name: string) {
		if (!confirm(`Delete ${name}?`)) return;

		await pumpkin.run(() => api(`reports/delete`, { body: { name } }));
		open = null;
		void loadList();
	}

	async function runBench() {
		const r = await pumpkin.run(() => api<typeof bench>('bench/speed', { body: {} }));

		if (r) bench = { ...(bench ?? { history: [] }), ...r, running: true } as typeof bench;
	}

	function title(file: string) {
		const base = file.replace(/\.md$/, '');
		const m = base.match(/^(\d{4}-\d{2}-\d{2})(?:[_ T-](\d{2}[-:]?\d{2}))?[_ -]*(.*)$/);
		const rest = (m ? m[3] : base).replace(/[_-]+/g, ' ').trim();

		return {
			date: m ? new Date(`${m[1]}T00:00:00`).toLocaleDateString(undefined, { day: 'numeric', month: 'short' }) + (m[2] ? ` ${m[2].replace(/(\d{2})(\d{2})/, '$1:$2')}` : '') : '',
			title: rest ? rest.charAt(0).toUpperCase() + rest.slice(1) : base
		};
	}
</script>

{#if open}
	<div class="flex flex-col gap-3">
		<div class="flex items-center justify-between">
			<button class="-ml-1 flex h-9 items-center gap-1 rounded-full pr-3 pl-1 text-sm font-medium hover:bg-muted" onclick={() => (open = null)} type="button">
				<ChevronLeft class="h-4 w-4" /> Reports
			</button>
			{#if tab === 'reports'}
				<Button aria-label="Delete report" class="size-9 rounded-full" onclick={() => remove(open!.name)} size="icon" variant="ghost"><Trash class="h-4 w-4" /></Button>
			{/if}
		</div>
		<article class="rounded-2xl border border-border/60 p-4 md:p-6">
			<MarkdownContent content={open.text} />
		</article>
	</div>
{:else}
	<div class="flex flex-col gap-4">
		<div class="-mx-4 overflow-x-auto px-4 [scrollbar-width:none]">
		<Segmented
			bind:value={tab}
			onchange={(v) => v !== 'speed' && v !== 'compare' && loadList()}
			options={[
				{ label: 'Task reports', value: 'reports' },
				{ label: 'Speed', value: 'speed' },
				{ label: 'Compare', value: 'compare' },
				{ label: 'Quality benchmarks', value: 'bench-files' }
			]}
		/>
		</div>

		{#if tab === 'speed'}
			<Card description="Measures how fast the running model writes and reads, so you can compare presets." title="Speed test">
				{#snippet actions()}
					<Button class="rounded-full" disabled={bench?.running || (pumpkin.status !== 'ready' && pumpkin.status !== 'busy')} onclick={runBench} size="sm">
						{#if bench?.running}<LoaderCircle class="h-4 w-4 animate-spin" /> Testing{:else}<Gauge class="h-4 w-4" /> Run test{/if}
					</Button>
				{/snippet}

				{#if bench?.error}<p class="mb-3 text-sm text-destructive">{bench.error}</p>{/if}

				{#if !bench?.history.length}
					<p class="text-sm text-muted-foreground">No results yet. Start a model and run the test.</p>
				{:else}
					<div class="overflow-x-auto">
						<table class="w-full text-sm">
							<thead class="text-left text-xs text-muted-foreground">
								<tr><th class="py-2 pr-3 font-medium">When</th><th class="py-2 pr-3 font-medium">Preset</th><th class="py-2 pr-3 text-right font-medium">Writing</th><th class="py-2 text-right font-medium">Reading</th></tr>
							</thead>
							<tbody class="divide-y divide-border/60">
								{#each bench.history as r, i (i)}
									<tr>
										<td class="py-2 pr-3 whitespace-nowrap text-muted-foreground">{r.time}</td>
										<td class="py-2 pr-3">{r.model}</td>
										<td class="py-2 pr-3 text-right font-medium tabular-nums whitespace-nowrap">{r.gen} t/s</td>
										<td class="py-2 text-right tabular-nums whitespace-nowrap text-muted-foreground">{r.prompt} t/s</td>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
				{/if}
			</Card>
			<SpecCard />
		{:else if tab === 'compare'}
			<Compare />
		{:else}
			<Card description={dir ? `Saved in ${dir}` : ''} title={tab === 'reports' ? 'Task reports' : 'Quality benchmarks'}>
				{#if items === null}
					<div class="flex justify-center py-8"><LoaderCircle class="h-5 w-5 animate-spin text-muted-foreground" /></div>
				{:else if !items.length}
					<Empty text={tab === 'reports' ? 'No reports yet. Every task run saves one here.' : 'No quality benchmark results found.'} />
				{:else}
					<div class="flex flex-col divide-y divide-border/60">
						{#each items as f (f.name)}
							{@const t = title(f.name)}
							<button class="flex items-center gap-3 py-3 text-left hover:bg-muted/40" onclick={() => show(f.name)} type="button">
								<FileText class="h-4 w-4 shrink-0 text-muted-foreground" />
								<span class="min-w-0 flex-1 truncate text-sm font-medium">{t.title}</span>
								<span class="shrink-0 text-xs text-muted-foreground">{t.date}</span>
							</button>
						{/each}
					</div>
				{/if}
			</Card>
		{/if}
	</div>
{/if}
