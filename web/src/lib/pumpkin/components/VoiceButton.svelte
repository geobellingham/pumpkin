<script lang="ts">
	import { AudioLines, LoaderCircle, Square } from '@lucide/svelte';
	import { Button } from '$lib/components/ui/button';
	import { pumpkin } from '../store.svelte';
	import { pumpkinVoice as voice } from '../voice.svelte';

	const label = $derived(
		!voice.active ? 'Start voice chat' : voice.phase === 'speaking' ? 'Interrupt and talk' : 'End voice chat'
	);
</script>

{#if pumpkin.available && voice.supported}
	<Button
		aria-label={label}
		class={[
			'h-8 w-8 rounded-full p-0 max-md:h-9 max-md:w-9',
			voice.active && 'bg-foreground! text-background! hover:bg-foreground/90!'
		]}
		onclick={() => voice.press()}
		title={label}
		type="button"
		variant="ghost"
	>
		{#if voice.active && voice.phase === 'thinking'}
			<LoaderCircle class="h-4 w-4 animate-spin" />
		{:else if voice.active && voice.phase === 'speaking'}
			<Square class="h-3.5 w-3.5 fill-current" />
		{:else}
			<AudioLines class={['h-4 w-4', voice.active && voice.phase === 'listening' && 'animate-pulse']} />
		{/if}
	</Button>
{/if}
