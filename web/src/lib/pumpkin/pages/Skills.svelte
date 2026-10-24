<script lang="ts">
	// Skills: instructions for a kind of job, in the SKILL.md format. The model sees each skill's name and one line
	// and reads the rest (skills_read) when a request fits.
	import { Download, FileText, LoaderCircle, Pencil, Plus, Trash2 } from '@lucide/svelte';
	import { Button } from '$lib/components/ui/button';
	import * as Dialog from '$lib/components/ui/dialog';
	import { Input } from '$lib/components/ui/input';
	import { Switch } from '$lib/components/ui/switch';
	import Card from '../components/Card.svelte';
	import Field from '../components/Field.svelte';
	import Toggle from '../components/Toggle.svelte';
	import { api, pumpkin } from '../store.svelte';
	import type { Skill } from '../types';
	import { onMount } from 'svelte';

	let items = $state<Skill[] | null>(null);
	let on = $state(true);
	let folder = $state('');
	let editing = $state<{ body: string; description: string; name: string; old?: string; readonly?: boolean } | null>(null);
	let open = $state(false);
	let importing = $state(false);
	let source = $state('');

	async function load() {
		const r = await pumpkin.run(() => api<{ folder: string; items: Skill[]; on: boolean }>('skills'));

		if (!r) return;

		items = r.items;
		on = r.on;
		folder = r.folder;
	}

	onMount(load);

	const SOURCE: Record<string, string> = { builtin: 'Built in', project: 'Project', yours: 'Yours' };

	function edit(s?: Skill) {
		editing = s
			? { body: s.body, description: s.description, name: s.name, old: s.source === 'yours' ? s.name : undefined, readonly: s.source !== 'yours' }
			: { body: '', description: '', name: '' };
		open = true;
	}

	async function save() {
		if (!editing) return;

		const r = await pumpkin.run(
			() => api('skills', { body: { body: editing!.body, description: editing!.description, name: editing!.name, old_name: editing!.old } }),
			'Saved'
		);

		if (r) {
			open = false;
			await load();
		}
	}

	async function toggle(s: Skill, v: boolean) {
		s.enabled = v;
		await pumpkin.run(() => api('skills/toggle', { body: { enabled: v, name: s.name } }));
	}

	async function remove(s: Skill) {
		await pumpkin.run(() => api('skills/delete', { body: { name: s.name } }), `Deleted ${s.name}`);
		await load();
	}

	async function setOn(v: boolean) {
		on = v;
		await pumpkin.run(() => api('settings', { body: { skills: v }, method: 'PUT' }), 'Saved. Restart the model to apply it.');
	}

	async function doImport() {
		const src = source.trim();

		if (!src) return;

		importing = true;

		const body = /^https?:\/\//.test(src) ? { url: src } : { folder: src };
		const r = await pumpkin.run(() => api<{ imported: string[] }>('skills/import', { body }));

		importing = false;

		if (r) {
			pumpkin.say(r.imported.length ? `Imported ${r.imported.join(', ')}` : 'Nothing new to import');
			source = '';
			await load();
		}
	}
</script>

<div class="flex flex-col gap-4">
	<Card description="Instructions for particular jobs. The model sees each skill's name and its one-line description, and reads the instructions when a request fits. That keeps them cheap, even on a small model." title="Skills">
		{#snippet actions()}
			<Button class="rounded-full" onclick={() => edit()} size="sm"><Plus class="h-4 w-4" /> New</Button>
		{/snippet}

		<Toggle checked={on} description="Works with presets that have tools on. Restart the model after changing this." label="Use skills" onchange={setOn} />

		{#if items === null}
			<div class="flex justify-center py-6"><LoaderCircle class="h-5 w-5 animate-spin text-muted-foreground" /></div>
		{:else}
			<ul class="mt-2 flex flex-col divide-y divide-border/60">
				{#each items as s (s.name)}
					<li class="flex items-start gap-3 py-3">
						<FileText class="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
						<button class="min-w-0 flex-1 text-left" onclick={() => edit(s)} type="button">
							<span class="flex flex-wrap items-center gap-2">
								<span class="text-sm font-medium">{s.name}</span>
								<span class="rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">{SOURCE[s.source]}</span>
								{#if s.files.length}<span class="text-[11px] text-muted-foreground">+{s.files.length} file{s.files.length === 1 ? '' : 's'}</span>{/if}
							</span>
							<span class="mt-0.5 line-clamp-2 block text-xs text-muted-foreground">{s.description || 'No description: the model will not know when to use it.'}</span>
						</button>
						<div class="flex shrink-0 items-center gap-1">
							{#if s.source === 'yours'}
								<Button aria-label="Edit {s.name}" class="size-8 rounded-full" onclick={() => edit(s)} size="icon" variant="ghost"><Pencil class="h-4 w-4" /></Button>
								<Button aria-label="Delete {s.name}" class="size-8 rounded-full" onclick={() => remove(s)} size="icon" variant="ghost"><Trash2 class="h-4 w-4" /></Button>
							{/if}
							<Switch aria-label="Use {s.name}" checked={s.enabled} onCheckedChange={(v) => toggle(s, v)} />
						</div>
					</li>
				{/each}
			</ul>
		{/if}
	</Card>

	<Card description="From a GitHub link (a skill folder or a whole repository of skills) or a folder on this computer. Skills written for Claude work as they are." title="Import skills">
		<div class="flex flex-col gap-2 sm:flex-row">
			<Input bind:value={source} class="min-w-0 flex-1" placeholder="https://github.com/owner/repo/tree/main/skills/my-skill or a folder path" />
			<Button class="shrink-0 rounded-full" disabled={importing || !source.trim()} onclick={doImport}>
				{#if importing}<LoaderCircle class="h-4 w-4 animate-spin" />{:else}<Download class="h-4 w-4" />{/if} Import
			</Button>
		</div>
		{#if folder}<p class="mt-2 text-xs break-all text-muted-foreground">Your skills are kept in {folder}. Pumpkin Code also uses a project's own .pumpkin/skills, .claude/skills or .agents/skills.</p>{/if}
	</Card>
</div>

<Dialog.Root bind:open>
	<Dialog.Content class="max-h-[90dvh] overflow-y-auto sm:max-w-2xl">
		{#if editing}
			<Dialog.Header>
				<Dialog.Title>{editing.readonly ? editing.name : editing.old ? 'Edit skill' : 'New skill'}</Dialog.Title>
				{#if editing.readonly}<Dialog.Description>A built-in skill. Save it under a new name to make your own version.</Dialog.Description>{/if}
			</Dialog.Header>

			<div class="flex flex-col gap-4">
				<Field hint="Short, like write-a-report." label="Name"><Input bind:value={editing.name} /></Field>
				<Field hint="When the model should use it. This line is all it sees until it opens the skill." label="Description">
					<Input bind:value={editing.description} placeholder="Use when the user asks for..." />
				</Field>
				<Field hint="What to do, step by step. Markdown." label="Instructions">
					<textarea bind:value={editing.body} class="min-h-64 w-full rounded-xl border border-input bg-transparent p-3 font-mono text-xs dark:bg-input/30"></textarea>
				</Field>
			</div>

			<Dialog.Footer class="gap-2">
				<Button class="rounded-full" onclick={() => (open = false)} variant="ghost">Cancel</Button>
				<Button class="rounded-full" disabled={!editing.name.trim() || !editing.body.trim() || (editing.readonly && items?.some((x) => x.name === editing!.name.trim()))} onclick={save}>
					{editing.readonly ? 'Save as my skill' : 'Save'}
				</Button>
			</Dialog.Footer>
		{/if}
	</Dialog.Content>
</Dialog.Root>
