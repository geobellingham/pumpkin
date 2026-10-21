<script lang="ts">
	import { ChevronDown, MessageSquareText, Pencil, Play, Plus, Trash } from '@lucide/svelte';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Textarea } from '$lib/components/ui/textarea';
	import Card from '../components/Card.svelte';
	import Field from '../components/Field.svelte';
	import { api, pumpkin } from '../store.svelte';
	import type { Prompt } from '../types';
	import { onMount } from 'svelte';

	let prompts = $state<Prompt[]>([]);
	let openId = $state<string | null>(null);
	let editing = $state<Prompt | null>(null);

	async function load() {
		prompts = (await pumpkin.run(() => api<{ items: Prompt[] }>('prompts')))?.items ?? [];
	}

	onMount(load);

	function toChat(p: Prompt) {
		pumpkin.sendToChat(p.prompt);
		location.hash = '#/';
	}

	async function runAsTask(p: Prompt) {
		if (await pumpkin.run(() => api('agent/run', { body: { name: p.title, prompt: p.prompt } }), 'Running as a task')) {
			location.hash = '#/pumpkin/tasks';
		}
	}

	async function save() {
		if (!editing) return;

		const e = editing;
		const r = e.id
			? await pumpkin.run(() => api(`prompts/${e.id}`, { body: e, method: 'PUT' }))
			: await pumpkin.run(() => api('prompts', { body: e }));

		if (r) {
			editing = null;
			void load();
		}
	}

	async function remove(p: Prompt) {
		if (!confirm(`Delete “${p.title}”?`)) return;

		await pumpkin.run(() => api(`prompts/${p.id}`, { method: 'DELETE' }));
		void load();
	}
</script>

<Card description="Prompts you use often. Put one in the chat box, or run it in the background as a task." title="Saved prompts">
	{#snippet actions()}
		<Button class="rounded-full" onclick={() => (editing = { id: '', prompt: '', title: '' })} size="sm"><Plus class="h-4 w-4" /> New</Button>
	{/snippet}

	<div class="flex flex-col gap-2">
		{#if editing && !editing.id}{@render editor()}{/if}

		{#each prompts as p (p.id)}
			{#if editing?.id === p.id}
				{@render editor()}
			{:else}
				<div class="rounded-xl border border-border/60">
					<button
						aria-expanded={openId === p.id}
						class="flex w-full items-center justify-between gap-3 px-3 py-3 text-left font-medium"
						onclick={() => (openId = openId === p.id ? null : p.id)}
						type="button"
					>
						<span class="min-w-0 truncate">{p.title}</span>
						<ChevronDown class={['h-4 w-4 shrink-0 text-muted-foreground transition-transform', openId === p.id && 'rotate-180']} />
					</button>

					{#if openId === p.id}
						<div class="border-t border-border/60 px-3 pt-2.5 pb-3">
							<p class="text-sm whitespace-pre-wrap text-muted-foreground">{p.prompt}</p>
							<div class="mt-3 flex flex-wrap gap-2">
								<Button class="rounded-full" onclick={() => toChat(p)} size="sm"><MessageSquareText class="h-3.5 w-3.5" /> Put in chat</Button>
								<Button class="rounded-full" disabled={pumpkin.state?.agent.busy} onclick={() => runAsTask(p)} size="sm" variant="secondary"><Play class="h-3.5 w-3.5" /> Run as task</Button>
								<Button aria-label="Edit" class="size-9 rounded-full" onclick={() => (editing = { ...p })} size="icon" variant="ghost"><Pencil class="h-4 w-4" /></Button>
								<Button aria-label="Delete" class="size-9 rounded-full" onclick={() => remove(p)} size="icon" variant="ghost"><Trash class="h-4 w-4" /></Button>
							</div>
						</div>
					{/if}
				</div>
			{/if}
		{/each}
	</div>
</Card>

{#snippet editor()}
	{#if editing}
		<div class="flex flex-col gap-3 rounded-xl border border-foreground/25 p-3">
			<Field label="Title"><Input bind:value={editing.title} /></Field>
			<Field label="Prompt"><Textarea bind:value={editing.prompt} class="min-h-28" /></Field>
			<div class="flex justify-end gap-2">
				<Button class="rounded-full" onclick={() => (editing = null)} size="sm" variant="ghost">Cancel</Button>
				<Button class="rounded-full" disabled={!editing.title.trim() || !editing.prompt.trim()} onclick={save} size="sm">Save</Button>
			</div>
		</div>
	{/if}
{/snippet}
