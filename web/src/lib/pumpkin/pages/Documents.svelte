<script lang="ts">
	import { Eye, LoaderCircle, Plus, RefreshCw, Search, X } from '@lucide/svelte';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import Card from '../components/Card.svelte';
	import Toggle from '../components/Toggle.svelte';
	import { api, pumpkin } from '../store.svelte';
	import type { Settings } from '../types';
	import { onDestroy, onMount } from 'svelte';

	interface Status {
		done: number;
		errors: number;
		files: number;
		folders: string[];
		indexing: boolean;
		readable: number;
		scans: number;
		todo: number;
	}

	interface Hit {
		file: string;
		place: string;
		text: string;
	}

	let st = $state<Status | null>(null);
	let settings = $state<Settings | null>(null);
	let query = $state('');
	let hits = $state<Hit[] | null>(null);
	let searching = $state(false);
	let newFolder = $state('');
	let timer: ReturnType<typeof setInterval> | undefined;
	let vision = $state<{ available: boolean; model: string | null; reading: string | null } | null>(null);
	let job = $state<{ done: number; error: string | null; result: number | null; state: string; total: number } | null>(null);

	async function load() {
		st = await api<Status>('docs').catch(() => st);
		vision = await api<typeof vision>('vision').catch(() => vision);
		const id = vision?.reading;
		if (id) {
			lastJob = id;
			job = await api<typeof job>(`jobs/${id}`).catch(() => job);
		} else if (job?.state === 'running') {
			// it finished: ask once how it ended, so an error is shown instead of looking done
			const end = lastJob ? await api<typeof job>(`jobs/${lastJob}`).catch(() => null) : null;
			job = end && end.state !== 'running' ? end : { ...job, state: 'done' };
			lastJob = null;
		}
	}

	async function readScans() {
		const r = await pumpkin.run(() => api<{ job: { id: string } }>('docs/scans', { body: {} }));

		if (r) {
			job = { done: 0, error: null, result: null, state: 'running', total: st?.scans ?? 0 };
			void load();
		}
	}

	let lastJob: string | null = null;

	onMount(() => {
		// the interval starts at once, so leaving before the first answer still clears it
		timer = setInterval(load, 5000);
		void api<Settings>('settings')
			.catch(() => null)
			.then((s) => (settings = s));
		void load();
	});

	onDestroy(() => clearInterval(timer));

	async function save(changes: Partial<Settings>) {
		const r = await pumpkin.run(() => api<Settings>('settings', { body: changes, method: 'PUT' }), 'Saved');

		if (r) settings = r;
		void load();
	}

	async function addFolder() {
		const d = newFolder.trim();

		if (!d || !settings) return;

		const before = settings.doc_folders ?? [];
		const r = await pumpkin.run(() => api<Settings>('settings', { body: { doc_folders: [...before, d] }, method: 'PUT' }), 'Folder added');

		if (r) {
			settings = r;
			newFolder = '';
		}
	}

	async function removeFolder(d: string) {
		if (!settings) return;

		await save({ doc_folders: (settings.doc_folders ?? []).filter((x) => x !== d) });
	}

	async function search() {
		if (!query.trim()) return;

		searching = true;
		hits = (await pumpkin.run(() => api<{ hits: Hit[] }>(`docs/search?q=${encodeURIComponent(query)}`)))?.hits ?? [];
		searching = false;
	}

	const summary = $derived.by(() => {
		if (!st) return '';

		const bits = [`${st.readable} ${st.readable === 1 ? 'document' : 'documents'} the AI can search`];

		if (st.scans) bits.push(`${st.scans} ${st.scans === 1 ? 'picture or scan' : 'pictures or scans'} without text`);
		if (st.errors) bits.push(`${st.errors} could not be read`);

		return bits.join(', ');
	});
</script>

<Card
	description="The AI searches your PDFs, Word files, slides, spreadsheets, notes and emails here, answers from them and says which file and page each fact came from. Reading happens on this device."
	title="Your documents"
>
	{#snippet actions()}
		<Button class="rounded-full" onclick={() => pumpkin.run(() => api('docs/refresh', { body: {} })).then(load)} size="sm" variant="ghost">
			{#if st?.indexing}<LoaderCircle class="h-4 w-4 animate-spin" />{:else}<RefreshCw class="h-4 w-4" />{/if} Check for changes
		</Button>
	{/snippet}

	{#if st}
		<p class="text-sm">{summary}.</p>
		{#if st.indexing && st.todo}
			<p class="text-xs text-muted-foreground">Reading new files: {st.done} of {st.todo}</p>
		{/if}
		{#if vision?.reading && job}
			<p class="mt-2 flex items-center gap-2 text-xs text-muted-foreground">
				<LoaderCircle class="h-3 w-3 animate-spin" /> Reading scans with {vision.model}: {job.done} of {job.total || st.scans}
			</p>
		{:else if st.scans}
			<div class="mt-2 flex flex-wrap items-center gap-2">
				{#if vision?.available}
					<Button class="rounded-full" onclick={readScans} size="sm" variant="secondary">
						<Eye class="h-4 w-4" /> Read {st.scans === 1 ? 'the scan' : `${st.scans} scans`}
					</Button>
					<span class="text-xs text-muted-foreground">{vision.model} copies the text from each picture so the AI can search it.</span>
				{:else}
					<span class="text-xs text-muted-foreground">To search scans and photos of paper, download a model with vision on the Models page (Gemma 3 4B is a good small one).</span>
				{/if}
			</div>
		{/if}
		{#if job?.error}
			<p class="mt-1 text-xs text-destructive">{job.error}</p>
		{/if}
	{/if}

	<p class="mt-2 text-xs text-muted-foreground">
		Ask in the chat with Tools on, for example: “What does my lease say about the notice period?” or “Summarise the PDFs from the bank and save it as a Word file.”
	</p>

	<form
		class="mt-3 flex gap-2"
		onsubmit={(e) => {
			e.preventDefault();
			void search();
		}}
	>
		<Input bind:value={query} placeholder="Try a search, like “notice period”" />
		<Button aria-label="Search" class="size-9 shrink-0 rounded-full" disabled={searching || !query.trim()} size="icon" type="submit" variant="secondary">
			{#if searching}<LoaderCircle class="h-4 w-4 animate-spin" />{:else}<Search class="h-4 w-4" />{/if}
		</Button>
	</form>

	{#if hits}
		{#if !hits.length}
			<p class="mt-2 text-sm text-muted-foreground">Nothing matches. Try other words.</p>
		{:else}
			<ul class="mt-2 flex flex-col gap-2">
				{#each hits as h, i (i)}
					<li class="rounded-xl bg-muted/40 p-3">
						<p class="text-xs font-medium">{h.file}{h.place ? `, ${h.place}` : ''}</p>
						<p class="mt-1 line-clamp-4 text-sm text-muted-foreground">{h.text}</p>
					</li>
				{/each}
			</ul>
		{/if}
	{/if}

	{#if settings}
		<div class="mt-4 border-t border-border/60 pt-3">
			<Toggle bind:checked={settings.doc_index} description="Keeps new and changed files searchable, a few minutes after they appear." label="Make documents searchable" onchange={(v) => save({ doc_index: v })} />
			<p class="mt-3 text-sm font-medium">More folders</p>
			<p class="text-xs text-muted-foreground">Folders on this computer the AI may also search, like Documents. It only reads them, never changes them.</p>
			{#each settings.doc_folders ?? [] as d (d)}
				<div class="mt-2 flex items-center gap-2">
					<code class="min-w-0 flex-1 truncate rounded-lg bg-muted/60 px-2 py-1.5 text-xs">{d}</code>
					<Button aria-label="Remove {d}" class="size-8 shrink-0 rounded-full" onclick={() => removeFolder(d)} size="icon" variant="ghost"><X class="h-4 w-4" /></Button>
				</div>
			{/each}
			<form
				class="mt-2 flex gap-2"
				onsubmit={(e) => {
					e.preventDefault();
					void addFolder();
				}}
			>
				<Input bind:value={newFolder} class="font-mono text-xs" placeholder={pumpkin.state?.platform === 'win32' ? 'C:\\Users\\you\\Documents' : '~/Documents'} />
				<Button aria-label="Add folder" class="size-9 shrink-0 rounded-full" disabled={!newFolder.trim()} size="icon" type="submit" variant="secondary"><Plus class="h-4 w-4" /></Button>
			</form>
		</div>
	{/if}
</Card>
