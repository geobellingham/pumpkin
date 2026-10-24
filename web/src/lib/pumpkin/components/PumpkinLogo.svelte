<script lang="ts">
	import plain from '$lib/assets/logo.svg?raw';
	import jack from '$lib/assets/logo-halloween.svg?raw';
	import { isHalloween } from '../season';

	let { class: className = 'size-5' }: { class?: string } = $props();

	// a carved jack-o'-lantern during Halloween week, the plain pumpkin the rest of the year
	const mark = isHalloween() ? jack : plain;
</script>

<span aria-hidden="true" class={['pumpkin-logo inline-block shrink-0', className]}>{@html mark}</span>

<style>
	.pumpkin-logo :global(svg) {
		display: block;
		width: 100%;
		height: 100%;
	}
</style>
