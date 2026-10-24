<script lang="ts">
	let {
		class: className = '',
		label = '',
		max,
		values
	}: { class?: string; label?: string; max?: number; values: (number | null)[] } = $props();

	const W = 120;
	const H = 32;

	const path = $derived.by(() => {
		const pts = values.map((v, i) => ({ i, v }));
		const top = max ?? Math.max(1, ...values.map((v) => v ?? 0));
		const step = values.length > 1 ? W / (values.length - 1) : W;
		let line = '';
		let area = '';
		let open = false;

		for (const { i, v } of pts) {
			if (v == null) {
				open = false;

				continue;
			}

			const x = (i * step).toFixed(1);
			const y = (H - 2 - (Math.min(v, top) / top) * (H - 4)).toFixed(1);

			line += `${open ? 'L' : 'M'}${x} ${y} `;
			open = true;
		}

		if (line) {
			const first = pts.find((p) => p.v != null)!;
			const last = [...pts].reverse().find((p) => p.v != null)!;

			area = `${line} L${(last.i * step).toFixed(1)} ${H} L${(first.i * step).toFixed(1)} ${H} Z`;
		}

		return { area, line };
	});
</script>

<svg aria-label={label} class={['h-8 w-full overflow-visible text-orange-600 dark:text-orange-400', className]} preserveAspectRatio="none" role="img" viewBox="0 0 {W} {H}">
	{#if path.line}
		<path d={path.area} fill="currentColor" opacity="0.08" />
		<path d={path.line} fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" vector-effect="non-scaling-stroke" />
	{:else}
		<line opacity="0.25" stroke="currentColor" stroke-dasharray="3 4" vector-effect="non-scaling-stroke" x1="0" x2={W} y1={H - 2} y2={H - 2} />
	{/if}
</svg>
