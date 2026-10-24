<script lang="ts">
	import { Check, Pencil, Plus, Trash } from '@lucide/svelte';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Textarea } from '$lib/components/ui/textarea';
	import Card from '../components/Card.svelte';
	import Field from '../components/Field.svelte';
	import { api, pumpkin } from '../store.svelte';
	import type { Persona, Settings } from '../types';
	import { onMount } from 'svelte';

	let personas = $state<Persona[]>([]);
	let settings = $state<Settings | null>(null);
	let editing = $state<Persona | null>(null);
	let mcp = $state('');
	let mcpError = $state('');

	async function load() {
		const [p, s, m] = await Promise.all([
			pumpkin.run(() => api<{ items: Persona[] }>('personas')),
			pumpkin.run(() => api<Settings>('settings')),
			pumpkin.run(() => api<{ text: string }>('mcp'))
		]);

		personas = p?.items ?? [];
		settings = s;
		mcp = m?.text ?? '';
	}

	onMount(load);

	async function choose(p: Persona) {
		const r = await pumpkin.run(() => api<{ restart_needed: boolean }>('persona', { body: { id: p.id } }));

		if (r) {
			pumpkin.say(`${p.name} is on. New chats use it; chats you already started keep the old one.`);
			void pumpkin.refresh();
			void load();
		}
	}

	async function savePersona() {
		if (!editing) return;

		const e = editing;
		const r = e.id
			? await pumpkin.run(() => api(`personas/${e.id}`, { body: e, method: 'PUT' }))
			: await pumpkin.run(() => api('personas', { body: e }));

		if (r) {
			editing = null;
			void load();
		}
	}

	async function removePersona(p: Persona) {
		if (!confirm(`Delete the persona “${p.name}”?`)) return;

		await pumpkin.run(() => api(`personas/${p.id}`, { method: 'DELETE' }));
		void load();
	}

	async function saveSettings(patch: Partial<Settings>) {
		const r = await pumpkin.run(() => api<Settings>('settings', { body: patch, method: 'PUT' }), 'Saved');

		if (r) settings = r;
	}

	async function saveMcp() {
		mcpError = '';

		try {
			JSON.parse(mcp);
		} catch (e) {
			mcpError = `That is not valid JSON: ${(e as Error).message}`;

			return;
		}

		const r = await pumpkin.run(() => api<{ restart_needed: boolean }>('mcp', { body: { text: mcp }, method: 'PUT' }));

		if (r) pumpkin.say(r.restart_needed ? 'Saved. Restart the model to load the new servers.' : 'Saved');
	}

	const EXAMPLE = `{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/home/you/Documents"]
    }
  }
}`;
</script>

<div class="flex flex-col gap-4">
	<Card description="The system prompt every chat and task starts with." title="Personas">
		{#snippet actions()}
			<Button class="rounded-full" onclick={() => (editing = { id: '', name: '', prompt: '' })} size="sm"><Plus class="h-4 w-4" /> New</Button>
		{/snippet}

		<div class="flex flex-col gap-2">
			{#each personas as p (p.id)}
				{@const active = settings?.persona === p.name}
				{#if editing?.id === p.id}
					{@render editor()}
				{:else}
					<div class={['flex items-start gap-3 rounded-xl border px-3 py-3', active ? 'border-foreground/30 bg-muted/40' : 'border-border/60']}>
						<button class="min-w-0 flex-1 text-left" onclick={() => choose(p)} type="button">
							<div class="flex items-center gap-2 font-medium">
								{p.name}
								{#if active}<Check class="h-4 w-4 text-emerald-500" />{/if}
							</div>
							<div class="mt-0.5 line-clamp-2 text-xs text-muted-foreground">{p.prompt}</div>
						</button>
						<Button aria-label="Edit {p.name}" class="size-9 rounded-full" onclick={() => (editing = { ...p })} size="icon" variant="ghost"><Pencil class="h-4 w-4" /></Button>
						<Button aria-label="Delete {p.name}" class="size-9 rounded-full" onclick={() => removePersona(p)} size="icon" variant="ghost"><Trash class="h-4 w-4" /></Button>
					</div>
				{/if}
			{/each}

			{#if editing && !editing.id}{@render editor()}{/if}
		</div>
	</Card>

	{#if settings}
		<Card description="How far an agent task may go before it has to answer." title="Agent limits">
			<div class="grid gap-4 sm:grid-cols-2">
				<Field hint="Each step is one model turn, usually with a tool call." label="Steps per task">
					<Input bind:value={settings.max_steps} min="1" onchange={() => saveSettings({ max_steps: Number(settings!.max_steps) })} type="number" />
				</Field>
				<Field hint="Longer tool results are cut to keep the context small." label="Characters kept per tool result">
					<Input bind:value={settings.tool_chars} min="1000" onchange={() => saveSettings({ tool_chars: Number(settings!.tool_chars) })} step="1000" type="number" />
				</Field>
				<Field hint="Pumpkin Code: the most the model may write in one answer, in tokens. 0 means no limit: it writes as long as it needs, until its context is full." label="Longest answer in Pumpkin Code">
					<Input bind:value={settings.code_max_tokens} min="0" onchange={() => saveSettings({ code_max_tokens: Number(settings!.code_max_tokens) })} step="1024" type="number" />
				</Field>
				<Field hint="Pumpkin Code: tool calls it makes for one request before it checks in with you." label="Steps per request in Pumpkin Code">
					<Input bind:value={settings.code_max_steps} min="1" onchange={() => saveSettings({ code_max_steps: Number(settings!.code_max_steps) })} type="number" />
				</Field>
				<Field hint="The Docker image the model's commands run in. It is created fresh for each model start." label="Sandbox image">
					<Input bind:value={settings.sandbox_image} onchange={() => saveSettings({ sandbox_image: settings!.sandbox_image })} />
				</Field>
			</div>
		</Card>
	{/if}

	<Card description="Extra tools from MCP servers, in the same format as Cursor and Claude Desktop. Web search is built in and does not need to be listed." title="MCP servers">
		<Textarea bind:value={mcp} class="min-h-48 font-mono text-xs" spellcheck="false" />
		{#if mcpError}<p class="mt-2 text-sm text-destructive">{mcpError}</p>{/if}
		<div class="mt-3 flex flex-wrap gap-2">
			<Button class="rounded-full" onclick={saveMcp} size="sm">Save</Button>
			<Button class="rounded-full" onclick={() => (mcp = EXAMPLE)} size="sm" variant="ghost">Show an example</Button>
		</div>
	</Card>
</div>

{#snippet editor()}
	{#if editing}
		<div class="flex flex-col gap-3 rounded-xl border border-foreground/25 p-3">
			<Field label="Name"><Input bind:value={editing.name} placeholder="e.g. Travel planner" /></Field>
			<Field label="Instructions"><Textarea bind:value={editing.prompt} class="min-h-28" placeholder="You are…" /></Field>
			<div class="flex justify-end gap-2">
				<Button class="rounded-full" onclick={() => (editing = null)} size="sm" variant="ghost">Cancel</Button>
				<Button class="rounded-full" disabled={!editing.name.trim() || !editing.prompt.trim()} onclick={savePersona} size="sm">Save</Button>
			</div>
		</div>
	{/if}
{/snippet}
