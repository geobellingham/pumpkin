<script lang="ts">
	import type { Snippet } from 'svelte';

	let { children, text }: { children?: Snippet; text: string } = $props();
</script>

<div class="pk-rise flex flex-col items-center gap-3 rounded-[24px] bg-[var(--pk-soft)] px-6 py-10 text-center shadow-[var(--pk-soft-edge)]">
	<p class="max-w-sm text-sm text-muted-foreground">{text}</p>

	{@render children?.()}
</div>
