<script lang="ts">
	import { pumpkin } from '../store.svelte';

	let { class: className = '' }: { class?: string } = $props();

	const status = $derived(pumpkin.status);
</script>

<!-- a model that is up gets a green dot that pings; busy and loading are amber, off is grey, trouble is red -->
<span
	aria-hidden="true"
	class={[
		'inline-block size-2 shrink-0 rounded-full',
		status === 'ready' && 'pk-live',
		status === 'busy' && 'bg-amber-500',
		status === 'loading' && 'animate-pulse bg-amber-500',
		status === 'off' && 'bg-muted-foreground/50',
		(status === 'offline' || status === 'error') && 'bg-red-500',
		className
	]}
></span>
