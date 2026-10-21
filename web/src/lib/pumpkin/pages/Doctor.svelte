<script lang="ts">
	import { CircleAlert, CircleCheck, Copy, LoaderCircle, RefreshCw, TriangleAlert } from '@lucide/svelte';
	import { Button } from '$lib/components/ui/button';
	import Card from '../components/Card.svelte';
	import { api, pumpkin } from '../store.svelte';
	import type { Check } from '../types';
	import { onMount } from 'svelte';

	let checks = $state<Check[] | null>(null);

	async function load() {
		checks = null;
		checks = (await pumpkin.run(() => api<{ checks: Check[] }>('doctor')))?.checks ?? [];
	}

	onMount(load);

	const areas = $derived([...new Set((checks ?? []).map((c) => c.area))]);
	const problems = $derived((checks ?? []).filter((c) => c.status !== 'ok').length);

	async function copy(cmd: string) {
		try {
			await navigator.clipboard.writeText(cmd);
			pumpkin.say('Command copied. Paste it in a terminal.');
		} catch {
			pumpkin.say('Copy failed', 'error');
		}
	}
</script>

<div class="flex flex-col gap-4">
	<Card description={checks === null ? 'Checking…' : problems ? `${problems} thing${problems > 1 ? 's' : ''} to look at.` : 'Everything Pumpkin needs is in place.'} title="Doctor">
		{#snippet actions()}
			<Button aria-label="Check again" class="size-9 rounded-full" onclick={load} size="icon" variant="ghost"><RefreshCw class="h-4 w-4" /></Button>
		{/snippet}

		{#if checks === null}
			<div class="flex justify-center py-8"><LoaderCircle class="h-5 w-5 animate-spin text-muted-foreground" /></div>
		{:else}
			<div class="flex flex-col gap-5">
				{#each areas as area (area)}
					<div>
						<h3 class="mb-1 text-xs font-medium text-muted-foreground">{area}</h3>
						<div class="flex flex-col divide-y divide-border/60">
							{#each checks.filter((c) => c.area === area) as c (c.title)}
								<div class="flex gap-3 py-2.5">
									{#if c.status === 'ok'}
										<CircleCheck class="mt-0.5 h-4 w-4 shrink-0 text-emerald-500" />
									{:else if c.status === 'warn'}
										<TriangleAlert class="mt-0.5 h-4 w-4 shrink-0 text-amber-500" />
									{:else}
										<CircleAlert class="mt-0.5 h-4 w-4 shrink-0 text-red-500" />
									{/if}
									<div class="min-w-0 flex-1 text-sm">
										<div class="font-medium">{c.title}</div>
										<div class="text-muted-foreground break-words">{c.detail}</div>
										{#if c.status !== 'ok' && c.fix}<div class="mt-1">{c.fix}</div>{/if}
										{#if c.status !== 'ok' && c.command}
											<div class="mt-2 flex items-center gap-2 rounded-lg bg-muted/60 py-1 pr-1 pl-3">
												<code class="min-w-0 flex-1 truncate text-xs">{c.command}</code>
												<Button aria-label="Copy command" class="size-8 shrink-0 rounded-full" onclick={() => copy(c.command)} size="icon" variant="ghost"><Copy class="h-3.5 w-3.5" /></Button>
											</div>
										{/if}
									</div>
								</div>
							{/each}
						</div>
					</div>
				{/each}
			</div>
		{/if}
	</Card>
</div>
