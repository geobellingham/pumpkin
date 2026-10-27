<script lang="ts">
	import { Copy, LoaderCircle, Pencil, Play, Plus, Trash } from '@lucide/svelte';
	import { Button } from '$lib/components/ui/button';
	import * as Dialog from '$lib/components/ui/dialog';
	import { Input } from '$lib/components/ui/input';
	import Card from '../components/Card.svelte';
	import Empty from '../components/Empty.svelte';
	import Field from '../components/Field.svelte';
	import FitBadge from '../components/FitBadge.svelte';
	import Select from '../components/Select.svelte';
	import Toggle from '../components/Toggle.svelte';
	import { api, pumpkin } from '../store.svelte';
	import type { Connector, Fit, LocalModel, Preset } from '../types';
	import { folderLabel, madeFor, visionOptions } from '../vision-files';
	import { onMount } from 'svelte';

	let presets = $state<Preset[] | null>(null);
	let models = $state<LocalModel[]>([]);
	let editing = $state<Preset | null>(null);
	let open = $state(false);
	let fit = $state<Fit | null>(null);
	let saving = $state(false);
	let conns = $state<Connector[]>([]);

	async function load() {
		const [p, m] = await Promise.all([
			pumpkin.run(() => api<{ items: Preset[] }>('presets')),
			pumpkin.run(() => api<{ models: LocalModel[] }>('models'))
		]);

		presets = p?.items ?? [];
		models = m?.models ?? [];
		conns = ((await api<{ items: Connector[] }>('connectors').catch(() => null))?.items ?? []).filter((c) => c.enabled);
	}

	onMount(load);

	const modelList = $derived(models.filter((m) => m.kind === 'model'));
	const projList = $derived(models.filter((m) => m.kind === 'mmproj'));
	const selected = $derived(modelList.find((m) => m.path === editing?.model));
	const chosenProj = $derived(projList.find((p) => p.path === editing?.mmproj));

	// picking another model swaps the vision file for the one made for it (or none), so it never keeps the old model's
	let lastModel = '';

	$effect(() => {
		const e = editing;

		if (!e || !open) {
			lastModel = '';

			return;
		}

		if (lastModel && e.model !== lastModel) {
			const m = modelList.find((x) => x.path === e.model);
			const cur = projList.find((p) => p.path === e.mmproj);

			if (!cur || !madeFor(cur, m)) e.mmproj = projList.find((p) => madeFor(p, m) && p.path === m?.mmproj)?.path ?? projList.find((p) => madeFor(p, m))?.path ?? '';
		}

		lastModel = e.model;
	});
	const ctxOptions = $derived.by(() => {
		const max = selected?.info.ctx_train ?? 262144;
		const sizes = [4096, 8192, 16384, 32768, 65536, 98304, 131072, 196608, 262144].filter((s) => s <= max);

		if (editing && !sizes.includes(editing.ctx)) sizes.push(editing.ctx);

		return sizes.sort((a, b) => a - b).map((s) => ({ label: `${Math.round(s / 1024)}k tokens`, value: String(s) }));
	});

	$effect(() => {
		const e = editing;

		if (!e?.model || !open) {
			fit = null;

			return;
		}

		const q = `models/fit?path=${encodeURIComponent(e.model)}&ctx=${e.ctx}&kv=${e.kv || 'f16'}`;

		api<Fit>(q)
			.then((f) => (fit = f))
			.catch(() => (fit = null));
	});

	function edit(p?: Preset) {
		editing = p
			? { ...p }
			: {
					ctx: 32768,
					desc: '',
					extra: '',
					fitt: 1024,
					id: '',
					idle_minutes: 0,
					kv: 'q8_0',
					mmproj: '',
					model: modelList[0]?.path ?? '',
					name: 'New preset',
					sandbox: true,
					think_budget: null,
					tools: true,
					web: true
				};
		open = true;
	}

	async function save() {
		if (!editing) return;

		saving = true;

		const body = { ...editing, think_budget: editing.think_budget === null || (editing.think_budget as unknown) === '' ? null : Number(editing.think_budget) };
		const r = editing.id
			? await pumpkin.run(() => api<Preset & { restart_needed?: boolean }>(`presets/${editing!.id}`, { body, method: 'PUT' }))
			: await pumpkin.run(() => api<Preset>('presets', { body }));

		saving = false;

		if (r) {
			open = false;
			pumpkin.say('restart_needed' in r && r.restart_needed ? 'Saved. Restart the model to use the changes.' : 'Saved');
			void load();
		}
	}

	async function remove(p: Preset) {
		if (!confirm(`Delete the preset “${p.name}”? The model file stays.`)) return;

		await pumpkin.run(() => api(`presets/${p.id}`, { method: 'DELETE' }));
		void load();
	}

	async function duplicate(p: Preset) {
		const { id: _id, ...rest } = p;

		await pumpkin.run(() => api('presets', { body: { ...rest, name: `${p.name} copy` } }));
		void load();
	}

	const running = $derived(pumpkin.state?.server.preset?.id);
	const modelName = (path: string) => path.split('/').pop() ?? path;
</script>

<div class="flex flex-col gap-4">
	<Card description="A preset is a model plus how to run it: context size, memory settings and tools." title="Presets">
		{#snippet actions()}
			<Button class="rounded-full" disabled={!modelList.length} onclick={() => edit()} size="sm">
				<Plus class="h-4 w-4" /> New
			</Button>
		{/snippet}

		{#if presets === null}
			<div class="flex justify-center py-8"><LoaderCircle class="h-5 w-5 animate-spin text-muted-foreground" /></div>
		{:else if !presets.length}
			<Empty text="No presets yet. Install a model and Pumpkin makes a preset for it.">
				<Button class="rounded-full" href="#/pumpkin/models" size="sm">Install a model</Button>
			</Empty>
		{:else}
			<div class="flex flex-col divide-y divide-border/60">
				{#each presets as p (p.id)}
					<div class="flex flex-wrap items-center gap-x-3 gap-y-2 py-3">
						<div class="min-w-0 flex-1 basis-56">
							<div class="flex items-center gap-2">
								<span class="truncate font-medium">{p.name}</span>
								{#if running === p.id}<span class="rounded-full bg-emerald-500/15 px-2 py-0.5 text-[11px] font-medium text-emerald-700 dark:text-emerald-400">Running</span>{/if}
							</div>
							<div class="truncate text-xs text-muted-foreground">
								{modelName(p.model)} · {Math.round(p.ctx / 1024)}k{p.tools ? ' · tools' : ''}{p.web ? ' · web' : ''}{p.mmproj ? ' · vision' : ''}
							</div>
						</div>
						<div class="flex gap-1">
							{#if running !== p.id}
								<Button aria-label="Start {p.name}" class="size-9 rounded-full" onclick={() => pumpkin.startPreset(p.id)} size="icon" variant="secondary"><Play class="h-4 w-4" /></Button>
							{/if}
							<Button aria-label="Edit {p.name}" class="size-9 rounded-full" onclick={() => edit(p)} size="icon" variant="ghost"><Pencil class="h-4 w-4" /></Button>
							<Button aria-label="Duplicate {p.name}" class="size-9 rounded-full" onclick={() => duplicate(p)} size="icon" variant="ghost"><Copy class="h-4 w-4" /></Button>
							<Button aria-label="Delete {p.name}" class="size-9 rounded-full" onclick={() => remove(p)} size="icon" variant="ghost"><Trash class="h-4 w-4" /></Button>
						</div>
					</div>
				{/each}
			</div>
		{/if}
	</Card>
</div>

<Dialog.Root bind:open>
	<Dialog.Content class="flex max-h-[100dvh] flex-col gap-0 p-0 max-md:h-[100dvh]! max-md:w-screen! max-md:max-w-none! max-md:rounded-none! md:w-[34rem]! md:max-w-[calc(100vw-2rem)]!">
		{#if editing}
			<div class="flex items-center justify-between px-5 pt-[max(1.25rem,env(safe-area-inset-top))] pb-2">
				<Dialog.Title class="text-base font-semibold">{editing.id ? 'Edit preset' : 'New preset'}</Dialog.Title>
			</div>

			<div class="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-5 py-3">
				<Field label="Name"><Input bind:value={editing.name} /></Field>

				<Field label="Model">
					<Select bind:value={editing.model} options={modelList.map((m) => ({ label: `${m.file} (${m.size_gb} GB)`, value: m.path }))} />
				</Field>

				{#if projList.length}
					<Field hint="Lets the model look at images you attach. Only the vision file made for this model works with it." label="Vision file">
						<Select bind:value={editing.mmproj as string} options={visionOptions(projList, selected)} />
					</Field>
					{#if selected && !projList.some((p) => madeFor(p, selected))}
						<p class="-mt-2 text-xs text-muted-foreground">
							None of these were made for {selected.file}. It may be a text-only model, or its vision file is not downloaded. Pumpkin can still pass pictures to a vision model you have installed (Settings, "Let text-only models see pictures").
						</p>
					{:else if chosenProj && selected && !madeFor(chosenProj, selected)}
						<p class="-mt-2 text-xs text-amber-600 dark:text-amber-400">
							This vision file is from {folderLabel(chosenProj.folder)}, another model's folder. It probably won't work with {selected.file}.
						</p>
					{/if}
				{/if}

				<div class="grid grid-cols-2 gap-3">
					<Field label="Context">
						<Select options={ctxOptions} value={String(editing.ctx)} onchange={(v) => (editing!.ctx = Number(v))} />
					</Field>
					<Field label="Memory for context">
						<Select bind:value={editing.kv as string} options={[{ label: 'Smaller (q8_0)', value: 'q8_0' }, { label: 'Smallest (q4_0)', value: 'q4_0' }, { label: 'Full (f16)', value: 'f16' }]} />
					</Field>
				</div>

				{#if fit}
					<div class="flex items-center gap-2 text-xs text-muted-foreground">
						<FitBadge {fit} /> about {fit.need_gb} GB needed
					</div>
				{/if}

				<div class="flex flex-col gap-1 rounded-xl border border-border/60 px-3 py-2">
					<Toggle bind:checked={editing.tools as boolean} description="Your documents, memory, saving files and your other devices. With Docker, also commands and file edits in a sandbox." label="Tools" />
					<Toggle bind:checked={editing.web as boolean} description="Built-in web search and page reading, no API key needed." disabled={!editing.tools} label="Web search" />
					<Toggle
						checked={editing.spec !== false && !editing.mmproj}
						description={editing.mmproj
							? 'Off while this preset reads pictures (a Vision file is set).'
							: 'Guesses the next words from what is already in the chat and checks several at once. Same answers, faster when text repeats: quoting documents, editing code, summaries.'}
						disabled={!!editing.mmproj}
						label="Faster replies (speculative decoding)"
						onchange={(v) => editing && (editing.spec = v)}
					/>
					{#if editing.spec_result && !editing.mmproj}
						<p class={['-mt-1 pb-1 text-xs', editing.spec_result.verdict === 'slower' ? 'text-amber-600 dark:text-amber-400' : 'text-muted-foreground']}>
							Measured {new Date(editing.spec_result.at * 1000).toLocaleDateString()}: {editing.spec_result.text}
						</p>
					{:else if !editing.mmproj}
						<p class="-mt-1 pb-1 text-xs text-muted-foreground">
							Not measured for this preset yet: start it, then Reports > Speed > Measure.
						</p>
					{/if}
					{#if editing.tools && conns.length}
						<Toggle
							checked={editing.connectors == null}
							description="Off: pick which of your connectors this preset uses. Fewer tools help small models."
							label="Every connector that is on"
							onchange={(v) => editing && (editing.connectors = v ? null : conns.map((c) => c.id))}
						/>
						{#if editing.connectors != null}
							<div class="flex flex-wrap gap-x-4 gap-y-1 pb-1 pl-1">
								{#each conns as c (c.id)}
									<label class="flex items-center gap-2 text-sm">
										<input
											checked={editing.connectors.includes(c.id)}
											class="size-4 accent-foreground"
											onchange={(e) => {
												if (!editing) return;
												const on = (e.currentTarget as HTMLInputElement).checked;
												const cur = editing.connectors ?? [];
												editing.connectors = on ? [...cur, c.id] : cur.filter((x) => x !== c.id);
											}}
											type="checkbox"
										/>
										{c.name}
									</label>
								{/each}
							</div>
						{/if}
					{/if}
				</div>

				<details class="rounded-xl border border-border/60 px-3 py-2 text-sm">
					<summary class="cursor-pointer py-1 font-medium">Advanced</summary>
					<div class="mt-3 flex flex-col gap-4 pb-2">
						<Field hint="Caps how long the model thinks before answering. Empty means no limit, 0 turns thinking off." label="Thinking budget (tokens)">
							<Input bind:value={editing.think_budget} min="0" placeholder="No limit" type="number" />
						</Field>
						<Field hint="Frees GPU memory after this many idle minutes; the next message reloads it. 0 keeps it loaded." label="Unload when idle (minutes)">
							<Input bind:value={editing.idle_minutes} min="0" type="number" />
						</Field>
						<Field hint="Memory llama-server keeps free on the GPU when it decides how much of the model to put there." label="GPU headroom (MB)">
							<Input bind:value={editing.fitt} min="0" step="128" type="number" />
						</Field>
						<Field hint="Passed to llama-server as they are, for example --temp 0.6 --top-p 0.95" label="Extra llama-server options">
							<Input bind:value={editing.extra} placeholder="--temp 0.7" />
						</Field>
						<Field label="Description"><Input bind:value={editing.desc} /></Field>
					</div>
				</details>
			</div>

			<div class="flex justify-end gap-2 border-t border-border/60 px-5 py-3 pb-[max(0.75rem,env(safe-area-inset-bottom))]">
				<Button class="rounded-full" onclick={() => (open = false)} variant="ghost">Cancel</Button>
				<Button class="rounded-full" disabled={saving || !editing.model || !editing.name.trim()} onclick={save}>Save preset</Button>
			</div>
		{/if}
	</Dialog.Content>
</Dialog.Root>
