<script lang="ts">
	let { done = 0, total = 0, label = '' }: { done?: number; label?: string; total?: number } = $props();

	const pct = $derived(total ? Math.min(100, (done / total) * 100) : 0);
	const gb = (n: number) => (n / 2 ** 30).toFixed(n > 10 * 2 ** 30 ? 0 : 1);
</script>

<div>
	<div class="mb-1 flex justify-between gap-2 text-xs text-muted-foreground tabular-nums">
		<span class="truncate">{label}</span>
		{#if total}<span>{gb(done)} / {gb(total)} GB</span>{/if}
	</div>

	<div class="h-1.5 overflow-hidden rounded-full bg-foreground/10">
		<div
			class={['h-full rounded-full bg-foreground/80 transition-[width] duration-500', !total && 'w-1/3 animate-pulse']}
			style={total ? `width:${pct}%` : ''}
		></div>
	</div>
</div>
