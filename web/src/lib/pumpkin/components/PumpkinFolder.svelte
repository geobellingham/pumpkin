<script lang="ts">
	// Where Pumpkin keeps everything big: models, image models, your files, reports, saved conversations.
	// Picking a drive puts a Pumpkin folder on it; Browse picks any folder.
	import { ArrowLeft, Check, Folder, HardDrive, LoaderCircle, X } from '@lucide/svelte';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import Card from './Card.svelte';
	import { api, pumpkin } from '../store.svelte';
	import { onMount } from 'svelte';

	interface Place {
		free_gb: number;
		label: string;
		path: string;
		total_gb: number;
	}

	let { compact = false }: { compact?: boolean } = $props();

	let current = $state('');
	let places = $state<Place[]>([]);
	let typed = $state('');
	let saving = $state(false);
	let browsing = $state<{ folders: { name: string; path: string }[]; parent: string | null; path: string } | null>(null);

	const sep = $derived(current.includes('\\') ? '\\' : '/');

	async function load() {
		const r = await pumpkin.run(() => api<{ current: string; places: Place[] }>('setup/places'));

		if (!r) return;

		current = r.current;
		typed = r.current;
		places = r.places;
	}

	onMount(load);

	function inside(p: Place) {
		const root = p.path.replace(/[\\/]+$/, '');

		return current === root || current.startsWith(root + sep) || (root.endsWith(':') && current.toUpperCase().startsWith(root.toUpperCase()));
	}

	async function use(path: string) {
		saving = true;

		const r = await pumpkin.run(() => api<{ current: string }>('setup/folder', { body: { path } }), 'Pumpkin\'s folder is set');

		saving = false;

		if (r) {
			browsing = null;
			await load();
		}
	}

	async function browse(path: string) {
		browsing = await pumpkin.run(() => api<typeof browsing>(`setup/folders?path=${encodeURIComponent(path)}`));
	}
</script>

<Card
	description="Models are big, 5 to 50 GB each, so pick a drive with room. Models, image models, your files, reports and saved conversations all go into this one folder."
	title="Where Pumpkin keeps its files"
>
	<div class="flex flex-col gap-3">
		{#if places.length}
			<div class={['grid gap-2', compact ? 'sm:grid-cols-2' : 'sm:grid-cols-2 lg:grid-cols-3']}>
				{#each places as p (p.path)}
					{@const on = inside(p)}
					<button
						class={[
							'flex items-center gap-3 rounded-xl border p-3 text-left transition-colors focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none',
							on ? 'border-foreground/40 bg-muted/60' : 'border-border/60 hover:bg-muted/40'
						]}
						disabled={saving}
						onclick={() => use(p.path)}
						type="button"
					>
						<HardDrive class="size-5 shrink-0 text-muted-foreground" />
						<span class="min-w-0 flex-1">
							<span class="block truncate text-sm font-medium">{p.label}</span>
							<span class="block text-xs text-muted-foreground tabular-nums">{p.free_gb.toFixed(0)} GB free of {p.total_gb.toFixed(0)} GB</span>
						</span>
						{#if on}<Check class="size-4 shrink-0" />{/if}
					</button>
				{/each}
			</div>
		{/if}

		<div class="flex flex-col gap-2 sm:flex-row">
			<Input bind:value={typed} class="min-w-0 flex-1 font-mono text-xs" placeholder="D:\Pumpkin" />
			<div class="flex shrink-0 gap-2">
				<Button class="rounded-full" onclick={() => browse(typed || current)} variant="secondary"><Folder class="h-4 w-4" /> Browse</Button>
				<Button class="rounded-full" disabled={saving || !typed.trim() || typed.trim() === current} onclick={() => use(typed.trim())}>
					{#if saving}<LoaderCircle class="h-4 w-4 animate-spin" />{/if} Use this folder
				</Button>
			</div>
		</div>

		{#if browsing}
			<div class="rounded-xl border border-border/60">
				<div class="flex items-center gap-2 border-b border-border/60 px-3 py-2 text-xs">
					<span class="min-w-0 flex-1 truncate font-medium">{browsing.path}</span>
					<Button class="h-7 rounded-full text-xs" onclick={() => use(browsing!.path)} size="sm">Use this folder</Button>
					<button aria-label="Close" class="rounded-full p-1 hover:bg-muted" onclick={() => (browsing = null)} type="button"><X class="h-4 w-4" /></button>
				</div>
				<ul class="max-h-64 overflow-auto py-1 text-sm">
					{#if browsing.parent}
						<li><button class="flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-muted/60" onclick={() => browse(browsing!.parent!)} type="button"><ArrowLeft class="h-4 w-4 text-muted-foreground" /> Up</button></li>
					{/if}
					{#each browsing.folders as f (f.path)}
						<li>
							<button class="flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-muted/60" onclick={() => browse(f.path)} type="button">
								<Folder class="h-4 w-4 text-muted-foreground" /><span class="truncate">{f.name}</span>
							</button>
						</li>
					{:else}
						<li class="px-3 py-2 text-muted-foreground">No folders here.</li>
					{/each}
				</ul>
			</div>
		{/if}

		<p class="text-xs text-muted-foreground">
			Now: <span class="font-mono break-all">{current || '…'}</span>. Picking a whole drive makes a Pumpkin folder on it. Anything already in the old folder stays there and keeps working.
		</p>
	</div>
</Card>
