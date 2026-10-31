<script lang="ts">
	let { done = 0, total = 0, label = '' }: { done?: number; label?: string; total?: number } = $props();

	const pct = $derived(total ? Math.min(100, (done / total) * 100) : 0);
	const gb = (n: number) => (n / 2 ** 30).toFixed(n > 10 * 2 ** 30 ? 0 : 1);
</script>

<div>
	<div class="mb-1 flex justify-between gap-2 text-xs text-muted-foreground tabular-nums">
		<span class="truncate">{label}</span>
		{#if total}<span class="font-mono text-[11px]">{gb(done)} / {gb(total)} GB</span>{/if}
	</div>

	<div class="h-1.5 overflow-hidden rounded-full bg-[var(--pk-track)]">
		<div
			class={['pk-bar h-full rounded-full bg-[linear-gradient(90deg,var(--pk-hot-2),var(--pk-hot-1))] shadow-[0_0_12px_rgba(255,140,60,0.6)]', !total && 'w-1/3 animate-pulse']}
			style={total ? `width:${pct}%` : ''}
		></div>
	</div>
</div>
