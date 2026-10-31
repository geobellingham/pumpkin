<script lang="ts">
	interface Props {
		onchange?: (v: string) => void;
		options: { label: string; value: string }[];
		value: string;
	}

	let { onchange, options, value = $bindable() }: Props = $props();

	// the highlight slides to the picked option instead of jumping
	let box: HTMLElement | undefined = $state();
	let ind = $state({ left: 0, width: 0, ready: false });

	function place() {
		const el = box?.querySelector<HTMLElement>('[aria-selected="true"]');
		if (!el) return;
		ind = { left: el.offsetLeft, width: el.offsetWidth, ready: ind.ready };
		if (!ind.ready) requestAnimationFrame(() => (ind.ready = true));
	}

	$effect(() => {
		void value;
		void options.length;
		void box;
		requestAnimationFrame(place);
	});
</script>

<div bind:this={box} class="relative inline-flex gap-1 rounded-full bg-[var(--pk-sunken)] p-1 shadow-[var(--pk-sunken-shadow)]" role="tablist">
	<span
		aria-hidden="true"
		class={['seg-ind', ind.ready && 'ready']}
		style="transform: translateX({ind.left}px); width: {ind.width}px; opacity: {ind.width ? 1 : 0}"
	></span>
	{#each options as o (o.value)}
		<button
			aria-selected={value === o.value}
			class={[
				'relative z-[1] h-8 rounded-full px-3.5 text-xs font-medium whitespace-nowrap transition-colors duration-300',
				value === o.value ? 'text-[color:var(--pk-nav-active)]' : 'text-muted-foreground hover:text-foreground'
			]}
			onclick={() => {
				value = o.value;
				onchange?.(o.value);
			}}
			role="tab"
			type="button"
		>
			{o.label}
		</button>
	{/each}
</div>

<svelte:window onresize={place} />

<style>
	.seg-ind {
		position: absolute;
		top: 4px;
		left: 0;
		height: 32px;
		border-radius: 999px;
		background: var(--pk-nav-ind);
		box-shadow: var(--pk-nav-ind-shadow);
		pointer-events: none;
	}

	.seg-ind.ready {
		transition:
			transform 0.6s cubic-bezier(0.34, 1.35, 0.64, 1),
			width 0.4s var(--pk-smooth);
	}
</style>
