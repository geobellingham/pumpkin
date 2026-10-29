<script lang="ts">
	// Artifacts: what the model built for you (a page, a document, a diagram, code). The chat also shows them in a
	// panel while you talk; this page is the whole list.
	import { LoaderCircle, Plus } from '@lucide/svelte';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import Card from '../components/Card.svelte';
	import ArtifactView from '../components/ArtifactView.svelte';
	import Empty from '../components/Empty.svelte';
	import Toggle from '../components/Toggle.svelte';
	import { api, pumpkin } from '../store.svelte';
	import { onDestroy, onMount } from 'svelte';

	interface Item {
		by: string;
		id: string;
		kind: string;
		title: string;
		updated: number;
		version: number;
	}

	let items = $state<Item[] | null>(null);
	let enabled = $state(true);
	let open = $state<string | null>(null);
	let title = $state('');
	let timer: ReturnType<typeof setInterval> | undefined;

	async function load() {
		const r = await api<{ enabled: boolean; items: Item[] }>('artifacts').catch(() => null);

		if (!r) return;

		items = r.items;
		enabled = r.enabled;

		if (open && !r.items.some((i) => i.id === open)) open = null;
	}

	onMount(() => {
		void load();
		timer = setInterval(load, 5000);
	});

	onDestroy(() => clearInterval(timer));

	async function setEnabled(v: boolean) {
		await pumpkin.run(() => api('settings', { body: { artifacts: v }, method: 'PUT' }), 'Saved. Restart the model to apply it.');
	}

	async function add() {
		const t = title.trim();

		if (!t) return;

		const r = await pumpkin.run(() => api<Item>('artifacts', { body: { content: `# ${t}\n\n`, kind: 'md', title: t } }));

		if (r) {
			title = '';
			open = r.id;
			await load();
		}
	}

	const when = (t: number) => new Date(t * 1000).toLocaleDateString([], { day: 'numeric', month: 'short' });
	const KIND: Record<string, string> = { code: 'Code', html: 'Page', md: 'Document', svg: 'Diagram', text: 'Text' };
</script>

<div class="flex flex-col gap-4">
	<Card
		description="Things the model builds with you: a web page, a document, a diagram, a piece of code. They open in a panel next to the chat, where you can change them yourself or ask for changes. Every change is a new version, so you can go back."
		title="Artifacts"
	>
		<Toggle bind:checked={enabled} description="When it is off, the model cannot build these. Restart the model after changing it." label="Let the model build artifacts" onchange={setEnabled} />

		<form
			class="mt-3 flex gap-2"
			onsubmit={(e) => {
				e.preventDefault();
				void add();
			}}
		>
			<Input bind:value={title} maxlength={100} placeholder="Start a document yourself: give it a title" />
			<Button aria-label="Add" class="size-9 shrink-0 rounded-full" disabled={!title.trim()} size="icon" type="submit"><Plus class="h-4 w-4" /></Button>
		</form>
	</Card>

	{#if items === null}
		<div class="flex justify-center py-8"><LoaderCircle class="h-5 w-5 animate-spin text-muted-foreground" /></div>
	{:else if !items.length}
		<Empty text="Nothing yet. Ask the model for something to keep working on, like “make a small page that converts miles to kilometres”, in a chat with tools on." />
	{:else}
		<div class="grid gap-4 md:grid-cols-[16rem_1fr]">
			<ul class="flex max-h-[70vh] flex-col gap-1 overflow-auto">
				{#each items as it (it.id)}
					<li>
						<button
							class={['w-full rounded-xl px-3 py-2 text-left', open === it.id ? 'bg-muted' : 'hover:bg-muted/50']}
							onclick={() => (open = it.id)}
						>
							<span class="block truncate text-sm">{it.title}</span>
							<span class="text-xs text-muted-foreground">{KIND[it.kind] ?? it.kind} · v{it.version} · {when(it.updated)}</span>
						</button>
					</li>
				{/each}
			</ul>

			<div class="flex min-h-0 flex-col">
				{#if open}
					<ArtifactView id={open} ondeleted={() => { open = null; void load(); }} tall />
				{:else}
					<Empty text="Pick one on the left." />
				{/if}
			</div>
		</div>
	{/if}
</div>
