<script lang="ts">
	import { ChevronLeft, Download, Eye, LoaderCircle, RefreshCw, Search, Sparkles, Trash } from '@lucide/svelte';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import Card from '../components/Card.svelte';
	import Empty from '../components/Empty.svelte';
	import FitBadge from '../components/FitBadge.svelte';
	import Progress from '../components/Progress.svelte';
	import Segmented from '../components/Segmented.svelte';
	import { api, pumpkin } from '../store.svelte';
	import type { CatalogModel, Download as DL, HfFile, LocalModel, Plan } from '../types';
	import { onDestroy, onMount } from 'svelte';

	let tab = $state('install');
	let catalog = $state<CatalogModel[] | null>(null);
	let plans = $state<Record<string, Plan | 'gone' | undefined>>({});
	let hw = $state<{ ram_total: number; vram_total: number } | null>(null);
	let local = $state<LocalModel[] | null>(null);
	let dirs = $state<string[]>([]);
	let jobs = $state<DL[]>([]);
	let query = $state('');
	let results = $state<{ downloads: number; likes: number; repo: string }[] | null>(null);
	let suggestions = $state<string[]>([]);
	let searching = $state(false);
	let repo = $state<string | null>(null);
	let files = $state<HfFile[] | null>(null);
	let timer: ReturnType<typeof setInterval> | undefined;

	async function loadCatalog() {
		const r = await pumpkin.run(() =>
			api<{ hardware: { ram_total: number; vram_total: number }; models: CatalogModel[] }>('catalog')
		);

		if (!r) return;

		catalog = r.models;
		hw = r.hardware;

		for (const c of r.models) {
			if (c.plan) plans[c.id] = c.plan;
			else
				api<Plan>(`catalog/${c.id}`)
					.then((p) => (plans[c.id] = p))
					.catch((e) => (plans[c.id] = /no longer|404/.test(String(e?.message)) ? 'gone' : { error: String(e?.message ?? e) } as unknown as Plan));
		}
	}

	async function loadLocal(rescan = false) {
		const r = await pumpkin.run(() =>
			api<{ dirs: string[]; models: LocalModel[] }>(`models${rescan ? '?rescan=1' : ''}`)
		);

		if (r) {
			local = r.models;
			dirs = r.dirs;
		}
	}

	async function loadJobs() {
		const r = await api<{ jobs: DL[] }>('downloads').catch(() => null);

		if (r) jobs = r.jobs;
	}

	onMount(() => {
		void loadCatalog();
		void loadLocal();
		void loadJobs();
		api<{ suggestions: string[] }>('hf/suggestions')
			.then((r) => (suggestions = r.suggestions))
			.catch(() => {});
		pumpkin.fast++;
		timer = setInterval(() => {
			void loadJobs();

			if (tab === 'local' && jobs.some((j) => j.state === 'done')) void loadLocal();
		}, 1500);
	});

	onDestroy(() => {
		pumpkin.fast--;
		clearInterval(timer);
	});

	async function install(c: CatalogModel) {
		const r = await pumpkin.run(() => api(`catalog/${c.id}`, { body: { start: true } }), `Installing ${c.name}`);

		if (r) location.hash = '#/pumpkin/overview';
	}

	async function search(q = query) {
		query = q;

		if (!q.trim()) return;

		searching = true;
		repo = null;
		files = null;

		const r = await pumpkin.run(() => api<{ results: typeof results }>(`hf/search?q=${encodeURIComponent(q)}`));

		results = r?.results ?? [];
		searching = false;
	}

	async function openRepo(name: string) {
		repo = name;
		files = null;

		const r = await pumpkin.run(() => api<{ files: HfFile[] }>(`hf/files?repo=${encodeURIComponent(name)}`));

		files = r?.files ?? [];
	}

	async function download(f: HfFile, withProj: boolean) {
		const proj = withProj ? files?.find((x) => x.kind === 'mmproj') : undefined;
		const all = [...f.files, ...(proj ? proj.files : [])];

		await pumpkin.run(() => api('downloads', { body: { files: all, repo } }), `Downloading ${f.name}`);
		tab = 'downloads';
		void loadJobs();
	}

	async function makePreset(m: LocalModel) {
		const p = await pumpkin.run(() => api<{ name: string }>('presets/from-model', { body: { path: m.path } }));

		if (p) {
			pumpkin.say(`Preset “${p.name}” created`);
			location.hash = '#/pumpkin/presets';
		}
	}

	async function remove(m: LocalModel) {
		if (!confirm(`Delete ${m.file} (${m.size_gb} GB) from disk?`)) return;

		await pumpkin.run(() => api('models/delete', { body: { path: m.path } }), 'Deleted');
		void loadLocal(true);
	}

	const speed = (b: number) => (b > 0 ? `${(b / 2 ** 20).toFixed(1)} MB/s` : '');
	const fmtNum = (n: number) => (n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${Math.round(n / 1e3)}k` : `${n}`);
	const activeJobs = $derived(jobs.filter((j) => j.state === 'queued' || j.state === 'downloading').length);
</script>

<div class="flex flex-col gap-4">
	<div class="flex flex-wrap items-center justify-between gap-3">
		<Segmented
			bind:value={tab}
			onchange={(v) => v === 'local' && loadLocal()}
			options={[
				{ label: 'Install', value: 'install' },
				{ label: 'On this computer', value: 'local' },
				{ label: activeJobs ? `Downloads (${activeJobs})` : 'Downloads', value: 'downloads' }
			]}
		/>

		{#if hw}
			<span class="text-xs text-muted-foreground">
				Your computer: {hw.vram_total ? `${hw.vram_total.toFixed(0)} GB VRAM, ` : 'no GPU, '}{hw.ram_total.toFixed(0)} GB RAM
			</span>
		{/if}
	</div>

	{#if tab === 'install'}
		<Card description="Pick one and Pumpkin chooses the right size for your computer, downloads it, and starts it." title="One-click models">
			{#if catalog === null}
				<div class="flex justify-center py-8"><LoaderCircle class="h-5 w-5 animate-spin text-muted-foreground" /></div>
			{:else}
				<div class="grid gap-3 sm:grid-cols-2">
					{#each catalog as c (c.id)}
						{@const plan = plans[c.id]}
						{@const installing = pumpkin.state?.installs.find((i) => i.model === c.name && i.state === 'running')}
						{#if plan !== 'gone'}
							<div class="flex flex-col gap-2 rounded-xl border border-border/60 p-3.5">
								<div class="flex items-start justify-between gap-2">
									<div class="font-medium">{c.name}</div>
									{#if c.vision}<Eye class="h-4 w-4 shrink-0 text-muted-foreground" aria-label="Understands images" />{/if}
								</div>

								<p class="text-sm leading-snug text-muted-foreground">{c.blurb}</p>

								<div class="mt-auto flex items-center justify-between gap-2 pt-1">
									<div class="flex min-w-0 flex-col gap-1">
										{#if plan === undefined}
											<span class="text-xs text-muted-foreground">Checking the best size…</span>
										{:else if (plan as unknown as { error?: string }).error}
											<span class="text-xs text-muted-foreground">Can't check sizes right now (is Hugging Face reachable?)</span>
										{:else if plan.file}
											<FitBadge fit={plan.fit} short />
											<span class="text-xs text-muted-foreground">{plan.file.quant} · {plan.size_gb} GB</span>
										{:else}
											<span class="text-xs text-red-600 dark:text-red-400">Too big for this computer</span>
										{/if}
									</div>

									{#if c.installed}
										<span class="text-xs font-medium text-emerald-600 dark:text-emerald-400">Installed</span>
									{:else if installing}
										<span class="flex items-center gap-1.5 text-xs"><LoaderCircle class="h-3.5 w-3.5 animate-spin" />{installing.total ? Math.round((installing.done / installing.total) * 100) + '%' : 'Starting'}</span>
									{:else}
										<Button
											class="rounded-full"
											disabled={!plan || !plan.file || (plan.fit?.level === 'no')}
											onclick={() => install(c)}
											size="sm"
										>
											<Sparkles class="h-3.5 w-3.5" /> Install
										</Button>
									{/if}
								</div>
							</div>
						{/if}
					{/each}
				</div>
			{/if}
		</Card>

		<Card description="Search every GGUF model on Hugging Face. Each file shows whether it fits." title="Find more models">
			<form
				class="flex gap-2"
				onsubmit={(e) => {
					e.preventDefault();
					void search();
				}}
			>
				<Input aria-label="Search Hugging Face" bind:value={query} class="h-10 md:h-9" placeholder="e.g. Qwen3 8B, Gemma, Mistral" />
				<Button class="h-10 md:h-9" disabled={searching} type="submit">
					{#if searching}<LoaderCircle class="h-4 w-4 animate-spin" />{:else}<Search class="h-4 w-4" />{/if}
					Search
				</Button>
			</form>

			{#if suggestions.length && results === null}
				<div class="mt-3 flex flex-wrap gap-2">
					{#each suggestions as sug (sug)}
						<button class="rounded-full bg-muted/60 px-3 py-1 text-xs hover:bg-muted" onclick={() => search(sug)} type="button">{sug}</button>
					{/each}
				</div>
			{/if}

			{#if repo}
				<button class="mt-3 -ml-1 flex h-9 items-center gap-1 rounded-full pr-3 pl-1 text-sm font-medium hover:bg-muted" onclick={() => (repo = null)} type="button">
					<ChevronLeft class="h-4 w-4" /> Results
				</button>
				<div class="mt-1 mb-2 text-sm font-medium break-all">{repo}</div>
				{#if files === null}
					<div class="flex justify-center py-6"><LoaderCircle class="h-5 w-5 animate-spin text-muted-foreground" /></div>
				{:else if !files.length}
					<p class="text-sm text-muted-foreground">No GGUF files in this repository.</p>
				{:else}
					{@const hasProj = files.some((f) => f.kind === 'mmproj')}
					<div class="flex flex-col divide-y divide-border/60">
						{#each files.filter((f) => f.kind === 'model') as f (f.name)}
							<div class="flex items-center gap-3 py-2.5">
								<div class="min-w-0 flex-1">
									<div class="truncate text-sm font-medium">{f.quant ?? f.name}</div>
									<div class="truncate text-xs text-muted-foreground">{f.size_gb} GB{f.files.length > 1 ? ` · ${f.files.length} parts` : ''}</div>
								</div>
								<FitBadge fit={f.fit} short />
								<Button aria-label="Download {f.name}" class="size-9 rounded-full" onclick={() => download(f, hasProj)} size="icon" variant="secondary">
									<Download class="h-4 w-4" />
								</Button>
							</div>
						{/each}
					</div>
					{#if hasProj}<p class="mt-2 text-xs text-muted-foreground">This model can see images. Its vision file downloads with it.</p>{/if}
				{/if}
			{:else if results}
				{#if !results.length}
					<p class="mt-3 text-sm text-muted-foreground">Nothing found. Try fewer words, like just the model family.</p>
				{:else}
					<div class="mt-3 flex flex-col divide-y divide-border/60">
						{#each results as r (r.repo)}
							<button class="flex items-center gap-3 py-2.5 text-left hover:bg-muted/40" onclick={() => openRepo(r.repo)} type="button">
								<span class="min-w-0 flex-1 truncate text-sm">{r.repo}</span>
								<span class="shrink-0 text-xs text-muted-foreground tabular-nums">{fmtNum(r.downloads)} downloads</span>
							</button>
						{/each}
					</div>
				{/if}
			{/if}
		</Card>
	{:else if tab === 'local'}
		<Card description={dirs.length ? `Looking in ${dirs.join(', ')}` : ''} title="Models on this computer">
			{#snippet actions()}
				<Button aria-label="Scan folders again" class="size-9 rounded-full" onclick={() => loadLocal(true)} size="icon" variant="ghost">
					<RefreshCw class="h-4 w-4" />
				</Button>
			{/snippet}

			{#if local === null}
				<div class="flex justify-center py-8"><LoaderCircle class="h-5 w-5 animate-spin text-muted-foreground" /></div>
			{:else if !local.filter((m) => m.kind === 'model').length}
				<Empty text="No models found. Install one, or add the folder where you keep your .gguf files in Settings.">
					<Button class="rounded-full" onclick={() => (tab = 'install')} size="sm">Install a model</Button>
				</Empty>
			{:else}
				<div class="flex flex-col divide-y divide-border/60">
					{#each local.filter((m) => m.kind === 'model') as m (m.path)}
						<div class="flex flex-wrap items-center gap-x-3 gap-y-2 py-3">
							<div class="min-w-0 flex-1 basis-56">
								<div class="truncate text-sm font-medium" title={m.path}>{m.file}</div>
								<div class="truncate text-xs text-muted-foreground">
									{m.size_gb} GB{m.quant ? ` · ${m.quant}` : ''}{m.info.arch ? ` · ${m.info.arch}` : ''}{m.info.ctx_train ? ` · up to ${Math.round(m.info.ctx_train / 1024)}k context` : ''}{m.mmproj ? ' · vision' : ''}{!m.complete ? ' · incomplete' : ''}
								</div>
							</div>
							<FitBadge fit={m.fit} short />
							<div class="flex gap-1">
								<Button class="rounded-full" onclick={() => makePreset(m)} size="sm" variant="secondary">Make preset</Button>
								<Button aria-label="Delete {m.file}" class="size-9 rounded-full" onclick={() => remove(m)} size="icon" variant="ghost">
									<Trash class="h-4 w-4" />
								</Button>
							</div>
						</div>
					{/each}
				</div>
			{/if}
		</Card>
	{:else}
		<Card title="Downloads">
			{#snippet actions()}
				{#if jobs.some((j) => !['queued', 'downloading'].includes(j.state))}
					<Button class="rounded-full" onclick={() => api('downloads/clear', { body: {} }).then(loadJobs)} size="sm" variant="ghost">Clear finished</Button>
				{/if}
			{/snippet}

			{#if !jobs.length}
				<p class="text-sm text-muted-foreground">Nothing downloading.</p>
			{:else}
				<div class="flex flex-col gap-4">
					{#each jobs as j (j.id)}
						<div class="flex items-center gap-3">
							<div class="min-w-0 flex-1">
								<div class="mb-1 truncate text-sm font-medium">{j.current ?? j.files[0]}</div>
								{#if j.state === 'error'}
									<p class="text-xs text-destructive">{j.error}</p>
								{:else if j.state === 'done'}
									<p class="text-xs text-emerald-600 dark:text-emerald-400">Done · {j.dest}</p>
								{:else if j.state === 'cancelled'}
									<p class="text-xs text-muted-foreground">Cancelled. Start it again to resume.</p>
								{:else}
									<Progress done={j.done} label={j.state === 'queued' ? 'Waiting' : speed(j.speed)} total={j.total} />
								{/if}
							</div>
							{#if j.state === 'queued' || j.state === 'downloading'}
								<Button class="rounded-full" onclick={() => api('downloads/cancel', { body: { id: j.id } }).then(loadJobs)} size="sm" variant="ghost">Cancel</Button>
							{/if}
						</div>
					{/each}
				</div>
			{/if}
		</Card>
	{/if}
</div>
