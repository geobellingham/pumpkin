<script lang="ts">
	// Pumpkin Code: a coding agent that works in a project folder on this computer. It reads, changes files,
	// runs commands and tests, and asks first unless told otherwise. The same sessions run from `pumpkin code`.
	import {
		ArrowLeft,
		ArrowUp,
		Check,
		ChevronRight,
		CircleAlert,
		Folder,
		FolderGit2,
		LoaderCircle,
		Play,
		Square,
		SquareTerminal,
		Trash2,
		Undo2,
		X
	} from '@lucide/svelte';
	import { MarkdownContent } from '$lib/components/app';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import Card from '../components/Card.svelte';
	import Segmented from '../components/Segmented.svelte';
	import Select from '../components/Select.svelte';
	import { api, pumpkin } from '../store.svelte';
	import { onDestroy, onMount, tick } from 'svelte';

	interface Summary {
		created: number;
		id: string;
		mode: string;
		phase?: { kind: string; label: string; since: number } | null;
		preset: string | null;
		root: string;
		state: string;
		title: string;
		updated: number;
	}

	interface Ev {
		[k: string]: unknown;
		kind: string;
		n: number;
	}

	interface Pending {
		command?: string;
		danger?: boolean;
		diff?: string;
		id: string;
		kind: 'command' | 'edit';
		new_file?: boolean;
		path?: string;
	}

	interface View extends Summary {
		ctx: number;
		error: string | null;
		events: Ev[];
		last: number;
		live: { text: string; thinking: string; tools?: string[] } | null;
		pending: Pending | null;
		phase?: { kind: string; label: string; since: number } | null;
		streamed?: number;
		decoded?: { stalled: number; tokens: number } | null;
		shell: string;
		todo: { done: boolean; text: string }[];
		tokens: number;
		undo: number;
	}

	interface Info {
		android: boolean;
		home: string;
		presets: { id: string; name: string }[];
		recent: string[];
		running: string | null;
		sessions: Summary[];
	}

	const MODES = [
		{ label: 'Ask first', value: 'ask' },
		{ label: 'Edit freely', value: 'edits' },
		{ label: 'Never ask', value: 'auto' }
	];
	const MODE_HELP: Record<string, string> = {
		ask: 'It asks before it changes a file or runs a command.',
		auto: 'It works on its own. Commands run on this computer as you, and only obviously risky ones (sudo, git push, deleting folders) still ask.',
		edits: 'It changes files without asking and asks before it runs a command.'
	};

	let info = $state<Info | null>(null);
	let view = $state<View | null>(null);
	let events = $state<Ev[]>([]);
	let root = $state('');
	let mode = $state('ask');
	let preset = $state('');
	let task = $state('');
	let message = $state('');
	let note = $state('');
	let browsing = $state<{ folders: { git: boolean; name: string; path: string }[]; git: boolean; parent: string | null; path: string } | null>(null);
	let starting = $state(false);
	let timer: ReturnType<typeof setTimeout> | undefined;
	let feed: HTMLElement | undefined = $state();
	let stuck = true;

	async function loadInfo() {
		info = await api<Info>('code').catch(() => info);
		if (info && !root) root = info.recent[0] ?? '';
	}

	onMount(() => {
		void loadInfo();
		const open = new URLSearchParams(location.hash.split('?')[1] ?? '').get('session');
		if (open) void openSession(open);
	});

	let clock = $state(Date.now());
	const clockTimer = setInterval(() => (clock = Date.now()), 1000);

	let destroyed = false;
	onDestroy(() => {
		destroyed = true; // a request still on its way must not start the polling again
		clearTimeout(timer);
		clearInterval(clockTimer);
	});

	const seconds = $derived(view?.phase ? Math.max(0, Math.round(clock / 1000 + pumpkin.skew - view.phase.since)) : 0);
	const clockText = $derived(seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${String(seconds % 60).padStart(2, '0')}s`);
	const tokensNow = $derived(Math.max(view?.decoded?.tokens ?? 0, view?.streamed ?? 0));
	const speedNow = $derived(pumpkin.state?.metrics.predicted_tokens_seconds ?? null);

	async function openSession(id: string) {
		clearTimeout(timer);
		events = [];
		view = null;
		const v = await pumpkin.run(() => api<View>(`code/${id}`));
		if (!v || destroyed) return;
		events = v.events;
		view = v;
		mode = v.mode;
		preset = v.preset ?? '';
		stuck = true;
		await scroll();
		poll();
	}

	function closeSession() {
		clearTimeout(timer);
		view = null;
		events = [];
		void loadInfo();
	}

	async function poll() {
		clearTimeout(timer);
		if (!view) return;
		const id = view.id;
		const v = await api<View>(`code/${id}?after=${view.last}`).catch(() => null);
		if (destroyed || !view || view.id !== id) return;
		if (v) {
			// two polls can overlap (a button press while one is on its way): add each event once
			const top = events.length ? events[events.length - 1].n : 0;
			const fresh = v.events.filter((e) => e.n > top);
			if (fresh.length) events = [...events, ...fresh].slice(-600);
			view = { ...v, last: Math.max(view.last, v.last) };
			if (v.events.length || v.live) void scroll();
		}
		const busy = v && (v.state === 'running' || v.state === 'waiting');
		clearTimeout(timer); // the other poll may have set one meanwhile: keep a single chain
		timer = setTimeout(poll, busy ? 500 : 3000);
	}

	async function scroll() {
		await tick();
		if (feed && stuck) feed.scrollTop = feed.scrollHeight;
	}

	function onFeedScroll() {
		if (!feed) return;
		stuck = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 80;
	}

	async function start() {
		if (!root.trim()) return;
		starting = true;
		const v = await pumpkin.run(() =>
			api<View>('code', { body: { mode, preset: preset || null, root: root.trim(), text: task.trim() || undefined } })
		);
		starting = false;
		if (v) {
			task = '';
			await openSession(v.id);
		}
	}

	async function send() {
		if (!view || !message.trim()) return;
		const text = message.trim();
		message = '';
		stuck = true;
		const r = await pumpkin.run(() => api<View>(`code/${view!.id}/send`, { body: { after: view!.last, text } }));
		if (!r) message = text;
		poll();
	}

	async function answer(allow: 'always' | 'no' | 'yes') {
		if (!view?.pending) return;
		await pumpkin.run(() => api(`code/${view!.id}/answer`, { body: { allow, id: view!.pending!.id, note: allow === 'no' ? note : '' } }));
		note = '';
		poll();
	}

	async function setMode(m: string) {
		if (view) await pumpkin.run(() => api(`code/${view!.id}/mode`, { body: { mode: m } }));
		poll();
	}

	async function setModel(p: string) {
		if (view) await pumpkin.run(() => api(`code/${view!.id}/model`, { body: { preset: p || null } }), 'Model changed');
	}

	async function undo() {
		if (view) await pumpkin.run(() => api<{ path: string }>(`code/${view!.id}/undo`, { body: {} }), 'Change undone');
		poll();
	}

	let stopping = $state(false);

	async function stop() {
		if (!view || stopping) return;
		stopping = true;
		await pumpkin.run(() => api(`code/${view!.id}/stop`, { body: {} }));
		poll();
	}

	// the stop went through once the session is no longer busy
	$effect(() => {
		if (stopping && view && view.state !== 'running' && view.state !== 'waiting') stopping = false;
	});

	async function remove(id: string) {
		await pumpkin.run(() => api(`code/${id}`, { method: 'DELETE' }), 'Session deleted');
		void loadInfo();
	}

	async function browse(path: string) {
		browsing = await pumpkin.run(() => api<NonNullable<typeof browsing>>(`code/folders?path=${encodeURIComponent(path)}`));
	}

	function ago(t: number) {
		const s = Date.now() / 1000 - t;
		if (s < 60) return 'just now';
		if (s < 3600) return `${Math.round(s / 60)} min ago`;
		if (s < 86400) return `${Math.round(s / 3600)} h ago`;
		return new Date(t * 1000).toLocaleDateString();
	}

	function short(p: string) {
		const h = info?.home;
		return h && p.startsWith(h) ? '~' + p.slice(h.length) : p;
	}

	function argLine(e: Ev) {
		const a = (e.args ?? {}) as Record<string, unknown>;
		return String(a.path ?? a.pattern ?? a.command ?? a.query ?? a.url ?? '');
	}

	function diffLines(d: string) {
		return d
			.split('\n')
			.slice(2)
			.map((l) => ({ cls: l.startsWith('+') ? 'add' : l.startsWith('-') ? 'del' : l.startsWith('@@') ? 'hunk' : '', l }));
	}

	const VERB: Record<string, string> = {
		bash: 'Ran',
		edit: 'Changed',
		glob: 'Found',
		grep: 'Searched',
		ls: 'Listed',
		read: 'Read',
		read_webpage: 'Read page',
		todo: 'Plan',
		web_search: 'Searched the web',
		write: 'Wrote'
	};

	const busy = $derived(view?.state === 'running' || view?.state === 'waiting');
	const usedPct = $derived(view && view.ctx ? Math.min(100, Math.round((view.tokens / view.ctx) * 100)) : 0);
	const presetOptions = $derived([
		{ label: info?.running ? `The running model (${info.running})` : 'The default preset', value: '' },
		...(info?.presets ?? []).map((p) => ({ label: p.name, value: p.id }))
	]);
</script>

<style>
	.diff {
		font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
		font-size: 12px;
		line-height: 1.45;
	}

	.diff .add {
		background: color-mix(in oklab, #22c55e 16%, transparent);
	}

	.diff .del {
		background: color-mix(in oklab, #ef4444 16%, transparent);
	}

	.diff .hunk {
		color: var(--muted-foreground);
	}

	.caret::after {
		animation: blink 1s steps(2) infinite;
		content: '▍';
		margin-left: 1px;
	}

	@keyframes blink {
		to {
			visibility: hidden;
		}
	}

	@media (prefers-reduced-motion: reduce) {
		.caret::after {
			animation: none;
		}
	}
</style>

{#snippet diffView(d: string)}
	<pre class="diff max-h-80 overflow-auto rounded-lg bg-muted/40 py-2">{#each diffLines(d) as x, i (i)}<div class={['px-3 whitespace-pre', x.cls]}>{x.l || ' '}</div>{/each}</pre>
{/snippet}

{#if !view}
	<div class="flex flex-col gap-4">
		<Card
			description="Give it a project folder and a job, like “fix the failing test” or “add dark mode to the settings page”. It reads the code, changes files, runs commands and tests, and keeps going until it is done. Every change can be undone."
			title="Pumpkin Code"
		>
			{#if info?.android}
				<p class="text-sm text-muted-foreground">Pumpkin Code runs on a computer. Open this page from your computer's Pumpkin (on the Devices page) to use it from the phone.</p>
			{:else}
				<div class="flex flex-col gap-3">
					<label class="flex flex-col gap-1.5 text-sm">
						<span class="font-medium">Project folder</span>
						<div class="flex gap-2">
							<Input bind:value={root} placeholder="/home/you/projects/my-app" />
							<Button class="shrink-0 rounded-full" onclick={() => browse(root || info?.home || '')} variant="secondary">
								<Folder class="h-4 w-4" /> Browse
							</Button>
						</div>
					</label>

					{#if browsing}
						<div class="rounded-xl border border-border/60">
							<div class="flex items-center gap-2 border-b border-border/60 px-3 py-2 text-xs">
								<span class="min-w-0 flex-1 truncate font-medium">{short(browsing.path)}</span>
								<Button class="h-7 rounded-full text-xs" onclick={() => { root = browsing!.path; browsing = null; }} size="sm">
									Use this folder
								</Button>
								<button aria-label="Close" class="rounded-full p-1 hover:bg-muted" onclick={() => (browsing = null)} type="button"><X class="h-4 w-4" /></button>
							</div>
							<ul class="max-h-64 overflow-auto py-1 text-sm">
								{#if browsing.parent}
									<li><button class="flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-muted/60" onclick={() => browse(browsing!.parent!)} type="button"><ArrowLeft class="h-4 w-4 text-muted-foreground" /> Up</button></li>
								{/if}
								{#each browsing.folders as f (f.path)}
									<li>
										<button class="flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-muted/60" onclick={() => browse(f.path)} type="button">
											{#if f.git}<FolderGit2 class="h-4 w-4 text-orange-500" />{:else}<Folder class="h-4 w-4 text-muted-foreground" />{/if}
											<span class="truncate">{f.name}</span>
										</button>
									</li>
								{:else}
									<li class="px-3 py-2 text-muted-foreground">No folders here.</li>
								{/each}
							</ul>
						</div>
					{/if}

					{#if info?.recent?.length}
						<div class="flex flex-wrap gap-1.5">
							{#each info.recent.slice(0, 6) as r (r)}
								<button class={['rounded-full border px-3 py-1 text-xs', r === root ? 'border-foreground' : 'border-border/60 text-muted-foreground hover:text-foreground']} onclick={() => (root = r)} type="button">{short(r)}</button>
							{/each}
						</div>
					{/if}

					<div class="flex flex-wrap items-center gap-3">
						<Segmented bind:value={mode} options={MODES} />
						<span class="text-xs text-muted-foreground">{MODE_HELP[mode]}</span>
					</div>

					<label class="flex flex-col gap-1.5 text-sm">
						<span class="font-medium">Model</span>
						<Select ariaLabel="Model" bind:value={preset} options={presetOptions} />
						<span class="text-xs text-muted-foreground">Coding works best with a model that is good at tool calls (Qwen3 Coder, Qwen3.6, GLM, Devstral) and a context of 32k or more.</span>
					</label>

					<textarea
						bind:value={task}
						class="min-h-24 rounded-xl border border-input bg-transparent p-3 text-sm dark:bg-input/30"
						onkeydown={(e) => {
							if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) void start();
						}}
						placeholder="What should it do? (You can also start empty and type in the session.)"
					></textarea>

					<div class="flex flex-wrap items-center gap-3">
						<Button class="rounded-full" disabled={!root.trim() || starting} onclick={start}>
							{#if starting}<LoaderCircle class="h-4 w-4 animate-spin" />{:else}<Play class="h-4 w-4" />{/if} Start
						</Button>
						<span class="flex items-center gap-1.5 text-xs text-muted-foreground"><SquareTerminal class="h-3.5 w-3.5" /> In a terminal: <code>pumpkin code</code> in the project folder</span>
					</div>
				</div>
			{/if}
		</Card>

		{#if info?.sessions?.length}
			<Card title="Sessions">
				<ul class="-mx-2 flex flex-col">
					{#each info.sessions as s (s.id)}
						<li class="group flex items-center gap-2 rounded-xl px-2 py-2 hover:bg-muted/40">
							<button class="flex min-w-0 flex-1 flex-col text-left" onclick={() => openSession(s.id)} type="button">
								<span class="truncate text-sm font-medium">{s.title}</span>
								<span class="truncate text-xs text-muted-foreground">
									{short(s.root)}, {ago(s.updated)}{s.state === 'running' || s.state === 'waiting' ? ', working' : ''}
								</span>
							</button>
							<button aria-label="Delete session" class="rounded-full p-2 text-muted-foreground opacity-0 group-hover:opacity-100 hover:bg-muted focus:opacity-100" onclick={() => remove(s.id)} type="button"><Trash2 class="h-4 w-4" /></button>
							<ChevronRight class="h-4 w-4 text-muted-foreground" />
						</li>
					{/each}
				</ul>
			</Card>
		{/if}
	</div>
{:else}
	<div class="flex h-[calc(100dvh-8rem)] min-h-[28rem] flex-col overflow-hidden rounded-2xl border border-border/60">
		<header class="border-b border-border/60 px-3 py-2.5 md:px-4">
			<div class="flex items-center gap-2">
				<button aria-label="All sessions" class="shrink-0 rounded-full p-1.5 hover:bg-muted" onclick={closeSession} type="button"><ArrowLeft class="h-4 w-4" /></button>
				<div class="min-w-0 flex-1">
					<p class="truncate text-sm font-medium">{view.title || 'New session'}</p>
					<p class="truncate text-xs text-muted-foreground">{short(view.root)}</p>
				</div>

				{#if view.ctx}
					<div class="hidden w-40 shrink-0 sm:block" title="How much of the model's memory this session uses">
						<div class="mb-1 flex justify-between text-[11px] text-muted-foreground"><span>Memory</span><span class="tabular-nums">{usedPct}%</span></div>
						<div class="h-1.5 overflow-hidden rounded-full bg-foreground/10">
							<div class={['h-full rounded-full transition-[width] duration-500', usedPct > 72 ? 'bg-amber-500' : 'bg-foreground/70']} style="width: {usedPct}%"></div>
						</div>
					</div>
				{/if}

				<Button class="shrink-0 rounded-full" disabled={!view.undo || busy} onclick={undo} size="sm" title="Take back the last file change" variant="ghost"><Undo2 class="h-4 w-4" /> <span class="max-sm:hidden">Undo</span></Button>
			</div>

			<div class="mt-2 flex flex-wrap items-center gap-2 pl-9">
				<Segmented bind:value={mode} onchange={setMode} options={MODES} />
				<div class="w-full min-w-48 sm:w-64"><Select ariaLabel="Model" bind:value={preset} onchange={setModel} options={presetOptions} /></div>
			</div>
		</header>

		<div aria-live="polite" class={['flex items-center gap-2 border-b border-border/60 px-3 py-2 text-xs md:px-4', busy ? 'bg-muted/40' : 'text-muted-foreground']}>
			{#if stopping}
				<span class="size-2 shrink-0 animate-pulse rounded-full bg-muted-foreground"></span>
				<span class="font-medium">Stopping</span>
			{:else if view.state === 'waiting'}
				<span class="size-2 shrink-0 rounded-full bg-sky-500"></span>
				<span class="font-medium">Waiting for your answer</span>
			{:else if busy && view.phase}
				<span class="size-2 shrink-0 animate-pulse rounded-full bg-orange-500"></span>
				<span class="min-w-0 truncate"><span class="font-medium">{view.phase.label}</span></span>
				<span class="shrink-0 text-muted-foreground tabular-nums">{clockText}</span>
				{#if tokensNow && ['thinking', 'writing', 'calling', 'summarising'].includes(view.phase.kind)}
					<span class="shrink-0 text-muted-foreground tabular-nums">· {tokensNow} tokens{speedNow ? `, ${speedNow.toFixed(1)} a second` : ''}</span>
				{/if}
				{#if (view.decoded?.stalled ?? 0) >= 90}
					<span class="min-w-0 truncate text-amber-600 dark:text-amber-400">No new tokens for {Math.floor(view.decoded!.stalled / 60)}m {String(view.decoded!.stalled % 60).padStart(2, '0')}s. The model may be stuck. You can stop it.</span>
				{/if}
			{:else if busy}
				<span class="size-2 shrink-0 animate-pulse rounded-full bg-orange-500"></span><span class="font-medium">Working</span>
			{:else}
				<span class="size-2 shrink-0 rounded-full bg-emerald-500"></span><span>Ready for the next job</span>
			{/if}
			<span class="ml-auto hidden shrink-0 text-muted-foreground md:inline">{MODE_HELP[mode]}</span>
		</div>

		<div bind:this={feed} class="flex-1 overflow-y-auto px-3 py-4 md:px-5" onscroll={onFeedScroll}>
			<div class="mx-auto flex max-w-4xl flex-col gap-3">
				{#each events as e (e.n)}
					{#if e.kind === 'user'}
						<div class="ml-auto max-w-[85%] rounded-2xl bg-muted px-4 py-2.5 text-sm whitespace-pre-wrap">{e.text}</div>
					{:else if e.kind === 'assistant'}
						{#if e.thinking}
							<details class="text-xs text-muted-foreground">
								<summary class="cursor-pointer select-none">Thought it through</summary>
								<p class="mt-1 whitespace-pre-wrap">{e.thinking}</p>
							</details>
						{/if}
						{#if e.text}<div class="text-sm"><MarkdownContent content={String(e.text)} /></div>{/if}
					{:else if e.kind === 'tool' && e.name !== 'todo'}
						<details class="group rounded-xl border border-border/60 text-sm" open={!!e.diff && !e.declined}>
							<summary class="flex cursor-pointer list-none items-center gap-2 px-3 py-2 select-none">
								{#if e.ok}<Check class="h-4 w-4 shrink-0 text-green-600" />{:else}<CircleAlert class="h-4 w-4 shrink-0 text-destructive" />{/if}
								<span class="shrink-0 font-medium">{VERB[String(e.name)] ?? e.name}</span>
								<code class="min-w-0 truncate text-xs text-muted-foreground">{argLine(e)}</code>
								<ChevronRight class="ml-auto h-4 w-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-90" />
							</summary>
							<div class="px-3 pb-3">
								{#if e.diff && !e.declined}
									{@render diffView(String(e.diff))}
								{:else}
									<pre class="max-h-72 overflow-auto rounded-lg bg-muted/40 p-3 text-xs whitespace-pre-wrap">{e.result}</pre>
								{/if}
							</div>
						</details>
					{:else if e.kind === 'todo'}
						<ul class="rounded-xl border border-border/60 px-3 py-2 text-sm">
							{#each (e.items as { done: boolean; text: string }[]) ?? [] as it, i (i)}
								<li class={['flex items-start gap-2 py-0.5', it.done && 'text-muted-foreground line-through']}>
									<span class={['mt-1 size-3 shrink-0 rounded-sm border', it.done ? 'border-green-600 bg-green-600' : 'border-muted-foreground']}></span>{it.text}
								</li>
							{/each}
						</ul>
					{:else if e.kind === 'error'}
						<p class="flex items-start gap-2 rounded-xl bg-destructive/10 px-3 py-2 text-sm text-destructive"><CircleAlert class="mt-0.5 h-4 w-4 shrink-0" /> {e.text}</p>
					{:else if e.kind === 'info' || e.kind === 'compact'}
						<p class="text-center text-xs text-muted-foreground">{e.kind === 'compact' ? 'Summarised the earlier part to fit the model’s context.' : e.text}</p>
					{:else if e.kind === 'answer' && e.allow === 'no' && e.note}
						<p class="ml-auto max-w-[85%] rounded-2xl bg-muted px-4 py-2 text-sm">No: {e.note}</p>
					{/if}
				{/each}

				{#if view.live}
					{#if view.live.text}
						<div class="caret text-sm whitespace-pre-wrap">{view.live.text}</div>
					{:else}
						<p class="flex items-center gap-2 text-xs text-muted-foreground">
							<LoaderCircle class="h-3.5 w-3.5 animate-spin" />
							{view.live.tools?.length ? `Getting ready to ${view.live.tools.join(', ')}` : view.live.thinking ? 'Thinking' : 'Reading the request'}
						</p>
					{/if}
				{:else if view.state === 'running'}
					<p class="flex items-center gap-2 text-xs text-muted-foreground"><LoaderCircle class="h-3.5 w-3.5 animate-spin" /> Working</p>
				{/if}

				{#if view.pending}
					<div class="rounded-2xl border-2 border-orange-500/70 p-3">
						{#if view.pending.kind === 'edit'}
							<p class="mb-2 text-sm font-medium">{view.pending.new_file ? 'Create' : 'Change'} <code>{view.pending.path}</code>?</p>
							{@render diffView(view.pending.diff ?? '')}
						{:else}
							<p class="mb-2 text-sm font-medium">Run this command?{#if view.pending.danger}<span class="ml-1 text-destructive">It is hard to take back.</span>{/if}</p>
							<pre class="overflow-auto rounded-lg bg-muted/40 p-3 text-xs whitespace-pre-wrap">{view.pending.command}</pre>
						{/if}
						<div class="mt-3 flex flex-wrap items-center gap-2">
							<Button class="rounded-full" onclick={() => answer('yes')} size="sm">Yes</Button>
							<Button class="rounded-full" onclick={() => answer('always')} size="sm" variant="secondary">
								{view.pending.kind === 'edit' ? 'Yes to all file changes' : 'Always for this command'}
							</Button>
							<Input bind:value={note} class="h-8 min-w-40 flex-1 text-xs" placeholder="Or say what to do instead" />
							<Button class="rounded-full" onclick={() => answer('no')} size="sm" variant="ghost">No</Button>
						</div>
					</div>
				{/if}
			</div>
		</div>

		<form
			class="flex items-end gap-2 border-t border-border/60 p-3"
			onsubmit={(e) => {
				e.preventDefault();
				void send();
			}}
		>
			<textarea
				bind:value={message}
				class="max-h-48 min-h-10 flex-1 resize-none rounded-xl border border-input bg-transparent px-3 py-2 text-sm dark:bg-input/30"
				disabled={busy}
				onkeydown={(e) => {
					if (e.key === 'Enter' && !e.shiftKey) {
						e.preventDefault();
						void send();
					}
				}}
				placeholder={busy ? 'It is working. You can stop it.' : view.events.length > 2 || events.length > 2 ? 'Reply, or give the next job' : 'What should it do?'}
				rows="1"
			></textarea>
			{#if busy}
				<Button aria-label={stopping ? 'Stopping' : 'Stop'} class="size-10 shrink-0 rounded-full" disabled={stopping} onclick={stop} size="icon" type="button" variant="secondary">
					{#if stopping}<LoaderCircle class="h-4 w-4 animate-spin" />{:else}<Square class="h-4 w-4" />{/if}
				</Button>
			{:else}
				<Button aria-label="Send" class="size-10 shrink-0 rounded-full" disabled={!message.trim()} size="icon" type="submit"><ArrowUp class="h-4 w-4" /></Button>
			{/if}
		</form>
	</div>
{/if}
