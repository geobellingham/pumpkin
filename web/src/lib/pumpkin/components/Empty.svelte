<script lang="ts">
	import type { Snippet } from 'svelte';

	let { children, text }: { children?: Snippet; text: string } = $props();
</script>

<div class="flex flex-col items-center gap-3 rounded-2xl border border-dashed border-border/70 px-6 py-10 text-center">
	<p class="max-w-sm text-sm text-muted-foreground">{text}</p>

	{@render children?.()}
</div>
