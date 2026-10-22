<script lang="ts">
	import { Check, LoaderCircle, Pencil, Plus, Trash, X } from '@lucide/svelte';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import Card from '../components/Card.svelte';
	import Empty from '../components/Empty.svelte';
	import Toggle from '../components/Toggle.svelte';
	import { api, pumpkin } from '../store.svelte';
	import { onMount } from 'svelte';

	interface Item {
		at: number;
		id: string;
		source: string;
		text: string;
	}

	let enabled = $state(true);
	let items = $state<Item[] | null>(null);
	let draft = $state('');
	let editing = $state<string | null>(null);
	let editText = $state('');
	let filter = $state('');

	async function load() {
		const r = await pumpkin.run(() => api<{ enabled: boolean; items: Item[] }>('memory'));

		if (r) {
			enabled = r.enabled;
			items = r.items;
		}
	}

	onMount(load);

	async function setEnabled(v: boolean) {
		await pumpkin.run(() => api('settings', { body: { memory: v }, method: 'PUT' }), v ? 'Memory is on' : 'Memory is off');
	}

	async function add() {
		const text = draft.trim();

		if (!text) return;

		if (await pumpkin.run(() => api('memory', { body: { text } }), 'Saved')) {
			draft = '';
			void load();
		}
	}

	function startEdit(it: Item) {
		editing = it.id;
		editText = it.text;
	}

	async function saveEdit(it: Item) {
		if (await pumpkin.run(() => api(`memory/${it.id}`, { body: { text: editText }, method: 'PUT' }), 'Saved')) {
			editing = null;
			void load();
		}
	}

	async function remove(it: Item) {
		if (await pumpkin.run(() => api(`memory/${it.id}`, { method: 'DELETE' }), 'Forgotten')) void load();
	}

	async function clearAll() {
		if (!confirm('Forget everything Pumpkin remembers about you?')) return;

		if (await pumpkin.run(() => api('memory/clear', { body: {} }), 'Memory cleared')) void load();
	}

	const shown = $derived(
		(items ?? [])
			.filter((i) => !filter.trim() || i.text.toLowerCase().includes(filter.trim().toLowerCase()))
			.slice()
			.sort((a, b) => b.at - a.at)
	);

	const when = (t: number) => new Date(t * 1000).toLocaleDateString();
</script>

<div class="flex flex-col gap-4">
	<Card
		description="Facts about you that every new chat starts with: your name, work, people, projects and how you like answers. The model saves them when you tell it something lasting or ask it to remember. They stay in one file on this device."
		title="Memory"
	>
		{#snippet actions()}
			{#if items?.length}
				<Button class="rounded-full" onclick={clearAll} size="sm" variant="ghost">Forget everything</Button>
			{/if}
		{/snippet}

		<Toggle bind:checked={enabled} description="When it is off, chats start without these facts and the model cannot save new ones." label="Use memory" onchange={setEnabled} />

		<form
			class="mt-3 flex gap-2"
			onsubmit={(e) => {
				e.preventDefault();
				void add();
			}}
		>
			<Input bind:value={draft} maxlength={400} placeholder="Add something, like “I prefer short answers”" />
			<Button aria-label="Add memory" class="size-9 shrink-0 rounded-full" disabled={!draft.trim()} size="icon" type="submit"><Plus class="h-4 w-4" /></Button>
		</form>

		{#if items === null}
			<div class="flex justify-center py-8"><LoaderCircle class="h-5 w-5 animate-spin text-muted-foreground" /></div>
		{:else if !items.length}
			<div class="mt-3">
				<Empty text="Nothing yet. Tell the model something like “Remember that I'm vegetarian” in a chat with tools on, or add it above." />
			</div>
		{:else}
			{#if items.length > 8}
				<Input bind:value={filter} class="mt-3" placeholder="Find a memory" />
			{/if}
			<ul class="mt-2 flex flex-col divide-y divide-border/60">
				{#each shown as it (it.id)}
					<li class="flex items-start gap-2 py-2">
						{#if editing === it.id}
							<form
								class="flex flex-1 gap-2"
								onsubmit={(e) => {
									e.preventDefault();
									void saveEdit(it);
								}}
							>
								<Input bind:value={editText} maxlength={400} />
								<Button aria-label="Save" class="size-9 shrink-0 rounded-full" size="icon" type="submit"><Check class="h-4 w-4" /></Button>
								<Button aria-label="Cancel" class="size-9 shrink-0 rounded-full" onclick={() => (editing = null)} size="icon" variant="ghost"><X class="h-4 w-4" /></Button>
							</form>
						{:else}
							<div class="min-w-0 flex-1 pt-1.5">
								<p class="text-sm break-words">{it.text}</p>
								<p class="text-xs text-muted-foreground">{it.source === 'you' ? 'Added by you' : 'Saved in a chat'} · {when(it.at)}</p>
							</div>
							<Button aria-label="Edit" class="size-9 shrink-0 rounded-full" onclick={() => startEdit(it)} size="icon" variant="ghost"><Pencil class="h-4 w-4" /></Button>
							<Button aria-label="Forget" class="size-9 shrink-0 rounded-full" onclick={() => remove(it)} size="icon" variant="ghost"><Trash class="h-4 w-4" /></Button>
						{/if}
					</li>
				{/each}
			</ul>
		{/if}
	</Card>
</div>
