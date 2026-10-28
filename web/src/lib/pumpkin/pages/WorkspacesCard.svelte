<script lang="ts">
	// Workspaces: folders Pumpkin Code keeps coming back to, each with its own instructions, memory, connectors,
	// model and approval mode. A session started from one gets all of that.
	import { ArrowLeft, Briefcase, Folder, FolderGit2, LoaderCircle, Pencil, Play, Plus, Trash2, X } from '@lucide/svelte';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import Card from '../components/Card.svelte';
	import Segmented from '../components/Segmented.svelte';
	import Select from '../components/Select.svelte';
	import { api, pumpkin } from '../store.svelte';
	import { onMount } from 'svelte';

	let { home = '', onstart }: { home?: string; onstart: (id: string) => void } = $props();

	interface Workspace {
		connectors: string[];
		id: string;
		instructions: string;
		kind: string;
		memory?: string;
		mode: string;
		name: string;
		preset: string | null;
		root: string;
		skills: string[] | null;
		used: number;
	}

	interface Options {
		connectors: { id: string; name: string; ready: boolean }[];
		items: Workspace[];
		kinds: Record<string, string>;
		presets: { id: string; name: string }[];
		skills: string[];
	}

	const MODES = [
		{ label: 'Ask first', value: 'ask' },
		{ label: 'Edit freely', value: 'edits' },
		{ label: 'Never ask', value: 'auto' }
	];
	const KIND_LABELS: Record<string, string> = {
		code: 'Code',
		data: 'Spreadsheets and data',
		general: 'Any files',
		writing: 'Documents and writing'
	};

	let data = $state<Options | null>(null);
	let editing = $state<Workspace | null>(null);
	let isNew = $state(false);
	let saving = $state(false);
	let limitSkills = $state(false);
	let browsing = $state<{ folders: { git: boolean; name: string; path: string }[]; parent: string | null; path: string } | null>(null);

	async function load() {
		data = await api<Options>('workspaces').catch(() => data);
	}

	onMount(load);

	const kindOptions = $derived(Object.keys(data?.kinds ?? {}).map((k) => ({ label: KIND_LABELS[k] ?? k, value: k })));
	const presetOptions = $derived([{ label: 'The running model or the default preset', value: '' }, ...(data?.presets ?? []).map((p) => ({ label: p.name, value: p.id }))]);

	function blank(): Workspace {
		return { connectors: [], id: '', instructions: '', kind: 'code', memory: '', mode: 'ask', name: '', preset: null, root: '', skills: null, used: 0 };
	}

	function create() {
		editing = blank();
		isNew = true;
		limitSkills = false;
		browsing = null;
	}

	async function edit(w: Workspace) {
		const full = await pumpkin.run(() => api<Workspace>(`workspaces/${w.id}`));
		if (!full) return;
		editing = { ...full, preset: full.preset ?? null };
		isNew = false;
		limitSkills = Array.isArray(full.skills);
		browsing = null;
	}

	async function save() {
		if (!editing) return;
		saving = true;
		const e = editing;
		const body = {
			connectors: e.connectors,
			instructions: e.instructions,
			kind: e.kind,
			mode: e.mode,
			name: e.name,
			preset: e.preset || null,
			root: e.root,
			skills: limitSkills ? (e.skills ?? []) : null
		};
		const saved = await pumpkin.run(() =>
			isNew ? api<Workspace>('workspaces', { body }) : api<Workspace>(`workspaces/${e.id}`, { body, method: 'PUT' })
		);
		if (saved && !isNew) await pumpkin.run(() => api(`workspaces/${e.id}/memory`, { body: { text: e.memory ?? '' }, method: 'PUT' }));
		saving = false;
		if (saved) {
			editing = null;
			await load();
		}
	}

	async function remove(w: Workspace) {
		await pumpkin.run(() => api(`workspaces/${w.id}`, { method: 'DELETE' }), 'Workspace deleted. Its folder is untouched.');
		if (editing?.id === w.id) editing = null;
		await load();
	}

	async function browse(path: string) {
		browsing = await pumpkin.run(() => api<NonNullable<typeof browsing>>(`code/folders?path=${encodeURIComponent(path)}`));
	}

	function toggle(list: string[], v: string, on: boolean) {
		return on ? [...list.filter((x) => x !== v), v] : list.filter((x) => x !== v);
	}

	function short(p: string) {
		return home && p.startsWith(home) ? '~' + p.slice(home.length) : p;
	}
</script>

<Card
	description="A folder you come back to, with what belongs to it: instructions, a memory the model adds to, the connectors it may use, and the model and approval mode you like there. It works for code, documents, spreadsheets and any other files."
	title="Workspaces"
>
	{#if data === null}
		<div class="flex justify-center py-4"><LoaderCircle class="h-5 w-5 animate-spin text-muted-foreground" /></div>
	{:else if editing}
		{@const e = editing}
		<div class="flex flex-col gap-3">
			<label class="flex flex-col gap-1.5 text-sm">
				<span class="font-medium">Name</span>
				<Input bind:value={e.name} placeholder="Quarterly reports" />
			</label>

			<label class="flex flex-col gap-1.5 text-sm">
				<span class="font-medium">Folder</span>
				<div class="flex gap-2">
					<Input bind:value={e.root} placeholder="/home/you/Documents/reports" />
					<Button class="shrink-0 rounded-full" onclick={() => browse(e.root || home)} type="button" variant="secondary"><Folder class="h-4 w-4" /> Browse</Button>
				</div>
			</label>

			{#if browsing}
				<div class="rounded-xl border border-border/60">
					<div class="flex items-center gap-2 border-b border-border/60 px-3 py-2 text-xs">
						<span class="min-w-0 flex-1 truncate font-medium">{short(browsing.path)}</span>
						<Button class="h-7 rounded-full text-xs" onclick={() => { e.root = browsing!.path; browsing = null; }} size="sm" type="button">Use this folder</Button>
						<button aria-label="Close" class="rounded-full p-1 hover:bg-muted" onclick={() => (browsing = null)} type="button"><X class="h-4 w-4" /></button>
					</div>
					<ul class="max-h-56 overflow-auto py-1 text-sm">
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

			<label class="flex flex-col gap-1.5 text-sm">
				<span class="font-medium">Kind of work</span>
				<Select ariaLabel="Kind of work" bind:value={e.kind} options={kindOptions} />
				<span class="text-xs text-muted-foreground">It decides how the model is told to work: code is checked by running it, writing keeps your voice, data never overwrites the original.</span>
			</label>

			<label class="flex flex-col gap-1.5 text-sm">
				<span class="font-medium">Instructions</span>
				<textarea bind:value={e.instructions} class="min-h-24 rounded-xl border border-input bg-transparent p-3 text-sm dark:bg-input/30" placeholder="Write in British English. Figures are in euro. Never touch the archive folder."></textarea>
			</label>

			<div class="flex flex-wrap items-center gap-3">
				<Segmented bind:value={e.mode} options={MODES} />
				<div class="w-full min-w-48 sm:w-72"><Select ariaLabel="Model" value={e.preset ?? ''} onchange={(v) => (e.preset = v || null)} options={presetOptions} /></div>
			</div>

			<div class="flex flex-col gap-1.5 text-sm">
				<span class="font-medium">Connectors</span>
				{#if data.connectors.length}
					<div class="flex flex-wrap gap-x-4 gap-y-1.5">
						{#each data.connectors as c (c.id)}
							<label class={['flex items-center gap-2', !c.ready && 'text-muted-foreground']}>
								<input checked={e.connectors.includes(c.id)} onchange={(ev) => (e.connectors = toggle(e.connectors, c.id, ev.currentTarget.checked))} type="checkbox" />
								{c.name}{#if !c.ready}<span class="text-xs">(needs setup)</span>{/if}
							</label>
						{/each}
					</div>
					<span class="text-xs text-muted-foreground">The model asks before it uses a tool that acts outside the folder, unless it only reads or you chose “Never ask”.</span>
				{:else}
					<span class="text-xs text-muted-foreground">Add connectors on the Connectors page first.</span>
				{/if}
			</div>

			<div class="flex flex-col gap-1.5 text-sm">
				<label class="flex items-center gap-2 font-medium">
					<input bind:checked={limitSkills} onchange={() => { if (limitSkills && !e.skills) e.skills = []; }} type="checkbox" /> Only some skills
				</label>
				{#if limitSkills}
					<div class="flex flex-wrap gap-x-4 gap-y-1.5">
						{#each data.skills as n (n)}
							<label class="flex items-center gap-2">
								<input checked={(e.skills ?? []).includes(n)} onchange={(ev) => (e.skills = toggle(e.skills ?? [], n, ev.currentTarget.checked))} type="checkbox" />
								{n}
							</label>
						{/each}
					</div>
				{:else}
					<span class="text-xs text-muted-foreground">All your skills, plus the ones in the folder’s <code>.pumpkin/skills</code>.</span>
				{/if}
			</div>

			{#if !isNew}
				<label class="flex flex-col gap-1.5 text-sm">
					<span class="font-medium">Memory</span>
					<textarea bind:value={e.memory} class="min-h-24 rounded-xl border border-input bg-transparent p-3 text-sm dark:bg-input/30" placeholder="Nothing yet. The model adds short facts here as you work, and you can edit them."></textarea>
					<span class="text-xs text-muted-foreground">One fact per line. New sessions start with it.</span>
				</label>
			{/if}

			<div class="flex flex-wrap items-center gap-2">
				<Button class="rounded-full" disabled={saving || !e.root.trim()} onclick={save}>
					{#if saving}<LoaderCircle class="h-4 w-4 animate-spin" />{/if} {isNew ? 'Create workspace' : 'Save'}
				</Button>
				<Button class="rounded-full" onclick={() => (editing = null)} variant="ghost">Cancel</Button>
				{#if !isNew}
					<Button class="ml-auto rounded-full text-destructive" onclick={() => remove(e)} variant="ghost"><Trash2 class="h-4 w-4" /> Delete</Button>
				{/if}
			</div>
		</div>
	{:else}
		{#if data.items.length}
			<ul class="-mx-2 flex flex-col">
				{#each data.items as w (w.id)}
					<li class="group flex items-center gap-2 rounded-xl px-2 py-2 hover:bg-muted/40">
						<Briefcase class="h-4 w-4 shrink-0 text-muted-foreground" />
						<div class="min-w-0 flex-1">
							<p class="truncate text-sm font-medium">{w.name} <span class="ml-1 rounded-full bg-muted px-2 py-0.5 text-[11px] font-normal text-muted-foreground">{KIND_LABELS[w.kind] ?? w.kind}</span></p>
							<p class="truncate text-xs text-muted-foreground">{short(w.root)}{w.connectors.length ? `, ${w.connectors.length} connector${w.connectors.length === 1 ? '' : 's'}` : ''}</p>
						</div>
						<Button aria-label="Edit {w.name}" class="rounded-full" onclick={() => edit(w)} size="icon" variant="ghost"><Pencil class="h-4 w-4" /></Button>
						<Button class="rounded-full" onclick={() => onstart(w.id)} size="sm"><Play class="h-4 w-4" /> New session</Button>
					</li>
				{/each}
			</ul>
		{:else}
			<p class="mb-3 text-sm text-muted-foreground">None yet. Make one for a folder you work in often.</p>
		{/if}
		<Button class="mt-2 rounded-full" onclick={create} variant="secondary"><Plus class="h-4 w-4" /> New workspace</Button>
	{/if}
</Card>
