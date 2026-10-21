<script lang="ts">
	import { pumpkin } from '../store.svelte';

	let { class: className = '' }: { class?: string } = $props();

	const status = $derived(pumpkin.status);
</script>

<span
	aria-hidden="true"
	class={[
		'inline-block size-2 shrink-0 rounded-full',
		status === 'ready' && 'bg-emerald-500',
		status === 'busy' && 'bg-amber-500',
		status === 'loading' && 'animate-pulse bg-amber-500',
		status === 'off' && 'bg-muted-foreground/50',
		(status === 'offline' || status === 'error') && 'bg-red-500',
		className
	]}
></span>
