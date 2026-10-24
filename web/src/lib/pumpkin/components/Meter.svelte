<script lang="ts">
	let {
		cached,
		cachedLabel = 'model file cached',
		label,
		used,
		total
	}: { cached?: number | null; cachedLabel?: string; label: string; total?: number | null; used?: number | null } = $props();

	const pct = $derived(used && total ? Math.min(100, (used / total) * 100) : 0);
	const cachePct = $derived(cached && total ? Math.min(100 - pct, (cached / total) * 100) : 0);
</script>

<div class="min-w-0 flex-1">
	<div class="mb-1.5 flex items-baseline justify-between gap-2 text-xs text-muted-foreground">
		<span>{label}</span>
		<span class="tabular-nums">{#if total}{(used ?? 0).toFixed(1)} / {total.toFixed(0)} GB{:else}–{/if}</span>
	</div>

	<div class="flex h-1.5 overflow-hidden rounded-full bg-foreground/10">
		<div
			class={['h-full transition-[width] duration-500', pct > 92 ? 'bg-amber-500' : 'bg-foreground/80']}
			style="width: {pct}%"
		></div>
		{#if cachePct > 0}
			<div class="h-full bg-foreground/25 transition-[width] duration-500" style="width: {cachePct}%"></div>
		{/if}
	</div>

	{#if cached && cached > 0.2}
		<div class="mt-1 text-[11px] text-muted-foreground">
			{cached.toFixed(1)} GB {cachedLabel}. The system counts it as free and gives it back when something needs it.
		</div>
	{/if}
</div>
