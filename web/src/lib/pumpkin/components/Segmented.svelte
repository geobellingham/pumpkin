<script lang="ts">
	interface Props {
		onchange?: (v: string) => void;
		options: { label: string; value: string }[];
		value: string;
	}

	let { onchange, options, value = $bindable() }: Props = $props();
</script>

<div class="inline-flex gap-1 rounded-full bg-muted/60 p-1" role="tablist">
	{#each options as o (o.value)}
		<button
			aria-selected={value === o.value}
			class={[
				'h-8 rounded-full px-3.5 text-xs font-medium whitespace-nowrap transition-colors',
				value === o.value ? 'bg-background text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'
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
