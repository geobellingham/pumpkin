<script lang="ts">
	import { Button } from '$lib/components/ui/button';
	import { api, pumpkin } from '../store.svelte';
	import Card from './Card.svelte';
	import { onMount } from 'svelte';

	interface Found {
		count: number;
		display: string;
		id: string;
		path: string;
		size_gb: number;
		source: string;
	}

	let { onadded }: { onadded?: () => void } = $props();

	let found = $state<Found[]>([]);
	let picked = $state<Record<string, boolean>>({});

	onMount(async () => {
		const r = await api<{ found: Found[] }>('models/discover').catch(() => null);

		found = r?.found ?? [];
		picked = Object.fromEntries(found.map((f) => [f.id, true]));
	});

	async function add() {
		const ids = found.filter((f) => picked[f.id]).map((f) => f.id);
		const r = await pumpkin.run(() => api<{ added: string[] }>('models/discover', { body: { ids } }), 'Models added');

		if (r) {
			found = [];
			onadded?.();
		}
	}
</script>

{#if found.length}
	<Card description="Pumpkin can use these where they are. Nothing is copied or moved." title="Models you already have">
		<div class="flex flex-col divide-y divide-border/60">
			{#each found as f (f.id)}
				<label class="flex cursor-pointer items-center gap-3 py-2.5">
					<input bind:checked={picked[f.id]} class="size-4 accent-orange-600" type="checkbox" />
					<span class="min-w-0 flex-1">
						<span class="block text-sm font-medium">{f.source}</span>
						<span class="block truncate text-xs text-muted-foreground" title={f.path}>{f.display}</span>
					</span>
					<span class="shrink-0 text-xs text-muted-foreground tabular-nums">{f.count} {f.count === 1 ? 'model' : 'models'}, {f.size_gb} GB</span>
				</label>
			{/each}
		</div>
		<div class="mt-3">
			<Button class="rounded-full" disabled={!found.some((f) => picked[f.id])} onclick={add} size="sm">Use selected</Button>
		</div>
	</Card>
{/if}
