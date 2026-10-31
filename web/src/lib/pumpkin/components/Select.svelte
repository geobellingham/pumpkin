<script lang="ts">
	interface Props {
		ariaLabel?: string;
		onchange?: (value: string) => void;
		options: { label: string; value: string }[];
		value: string;
	}

	let { ariaLabel, onchange, options, value = $bindable() }: Props = $props();
</script>

<select
	aria-label={ariaLabel}
	bind:value
	class="pk-input h-10 w-full rounded-xl px-3 text-sm md:h-9"
	onchange={(e) => onchange?.((e.currentTarget as HTMLSelectElement).value)}
>
	{#each options as o (o.value)}
		<option class="bg-popover text-popover-foreground" value={o.value}>{o.label}</option>
	{/each}
</select>
