<script lang="ts">
	import { page } from '$app/state';
	import PumpkinShell from '$lib/pumpkin/pages/Shell.svelte';
</script>

<svelte:head>
	<title>Pumpkin</title>
</svelte:head>

<PumpkinShell section={(page.params as Record<string, string | undefined>).section ?? 'overview'} />
