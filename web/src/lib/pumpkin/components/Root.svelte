<script lang="ts">
	import { page } from '$app/state';
	import StatusChip from './StatusChip.svelte';
	import VoiceBar from './VoiceBar.svelte';
	import { pumpkin } from '../store.svelte';
	import { pumpkinVoice } from '../voice.svelte';
	import { onMount } from 'svelte';
	import { fly } from 'svelte/transition';

	const onPumpkinPage = $derived(page.route.id?.startsWith('/pumpkin') ?? false);

	onMount(() => {
		pumpkin.start();
		pumpkinVoice.init();
	});

	// on Pumpkin's own pages the chat's logo button (top left) hides: the page has its own Pumpkin title
	$effect(() => {
		document.documentElement.classList.toggle('pumpkin-page', onPumpkinPage);
	});

	// phone-only layout rules (see the style block)
	$effect(() => {
		document.documentElement.classList.toggle('pumpkin-phone', pumpkin.state?.platform === 'android');
	});

	// first run: send people to setup once
	let redirected = false;

	$effect(() => {
		const s = pumpkin.state;

		if (!s || redirected) return;

		redirected = true;

		if (!s.setup_done && !(s.has_server && s.has_models) && !onPumpkinPage) location.hash = '#/pumpkin/setup';
	});
</script>

<style>
	/* Pumpkin's pages cover the chat's logo button, so only one pumpkin shows at the top */
	:global(html.pumpkin-page [aria-label='Expand navigation']),
	:global(html.pumpkin-page [aria-label='Go to start']) {
		visibility: hidden;
		pointer-events: none !important;
	}

	/* native menus (drop-downs, date pickers, scrollbars) follow the dark theme instead of staying white */
	:global(html.dark) {
		color-scheme: dark;
	}

	/* nothing may be wider than the screen, otherwise the page wobbles sideways under the finger.
	   clip (not hidden) so the page keeps scrolling normally and sticky headers keep working */
	:global(html.pumpkin-phone),
	:global(html.pumpkin-phone body) {
		max-width: 100%;
		overflow-x: clip;
		overscroll-behavior-x: none;
	}
</style>

{#if pumpkin.available}
	{#if !onPumpkinPage}
		<StatusChip />

		<VoiceBar />
	{/if}

	{#if pumpkin.toast}
		<div
			class="pointer-events-none fixed inset-x-0 top-16 z-[60] flex justify-center px-4"
			transition:fly={{ duration: 150, y: -8 }}
		>
			<div
				class={[
					'pointer-events-auto max-w-md rounded-2xl px-4 py-2.5 text-sm shadow-lg backdrop-blur-xl',
					pumpkin.toast.kind === 'error' ? 'bg-destructive/90 text-white' : 'bg-foreground text-background'
				]}
				role={pumpkin.toast.kind === 'error' ? 'alert' : 'status'}
			>
				{pumpkin.toast.text}
			</div>
		</div>
	{/if}
{/if}
