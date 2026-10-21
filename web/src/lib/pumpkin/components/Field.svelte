<script lang="ts">
	import type { Snippet } from 'svelte';

	interface Props {
		children: Snippet;
		hint?: string;
		label: string;
	}

	let { children, hint, label }: Props = $props();
</script>

<label class="flex flex-col gap-1.5 text-sm">
	<span class="font-medium">{label}</span>

	{@render children()}

	{#if hint}<span class="text-xs leading-snug text-muted-foreground">{hint}</span>{/if}
</label>
