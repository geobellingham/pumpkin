<script lang="ts">
	let { label, used, total }: { label: string; total?: number | null; used?: number | null } = $props();

	const pct = $derived(used && total ? Math.min(100, (used / total) * 100) : 0);
</script>

<div class="min-w-0 flex-1">
	<div class="mb-1.5 flex items-baseline justify-between gap-2 text-xs text-muted-foreground">
		<span>{label}</span>
		<span class="tabular-nums">{#if total}{(used ?? 0).toFixed(1)} / {total.toFixed(0)} GB{:else}–{/if}</span>
	</div>

	<div class="h-1.5 overflow-hidden rounded-full bg-foreground/10">
		<div
			class={['h-full rounded-full transition-[width] duration-500', pct > 92 ? 'bg-amber-500' : 'bg-foreground/80']}
			style="width: {pct}%"
		></div>
	</div>
</div>
