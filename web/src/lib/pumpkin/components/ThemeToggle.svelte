<script lang="ts">
	// Light, dark or the system's choice, for the chat and Pumpkin's pages. Kept in this browser.
	import { Monitor, Moon, Sun } from '@lucide/svelte';
	import { settingsStore } from '$lib/stores/settings/index.svelte';

	const ORDER = ['system', 'light', 'dark'];
	const LABEL: Record<string, string> = { dark: 'Dark', light: 'Light', system: 'Same as the system' };

	const current = $derived(String(settingsStore.config.theme ?? 'system'));

	function next() {
		settingsStore.updateTheme(ORDER[(ORDER.indexOf(current) + 1) % ORDER.length]);
	}
</script>

<button
	aria-label="Theme: {LABEL[current] ?? current}. Change it"
	class="flex size-9 shrink-0 items-center justify-center rounded-full text-muted-foreground hover:bg-muted hover:text-foreground"
	onclick={next}
	title="Theme: {LABEL[current] ?? current}"
	type="button"
>
	{#if current === 'dark'}<Moon class="size-4" />{:else if current === 'light'}<Sun class="size-4" />{:else}<Monitor class="size-4" />{/if}
</button>
