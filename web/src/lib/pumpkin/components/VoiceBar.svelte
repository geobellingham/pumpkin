<script lang="ts">
	import { X } from '@lucide/svelte';
	import { pumpkinVoice as voice } from '../voice.svelte';
	import { fly } from 'svelte/transition';

	const text = $derived.by(() => {
		switch (voice.phase) {
			case 'listening':
				return voice.heard || 'Listening…';
			case 'thinking':
				return voice.heard ? `“${voice.heard}”` : 'Thinking…';
			case 'speaking':
				return 'Speaking. Tap the square to cut in.';
			default:
				return '';
		}
	});
</script>

{#if voice.active || voice.problem}
	<div
		class="pointer-events-none fixed inset-x-0 z-20 flex justify-center px-4"
		style="bottom: calc(var(--chat-form-height, 7rem) + 1.75rem)"
		transition:fly={{ duration: 150, y: 8 }}
	>
		<div
			aria-live="polite"
			class="pointer-events-auto flex max-w-[min(36rem,100%)] items-center gap-2.5 rounded-full bg-muted/80 py-1.5 pr-1.5 pl-4 text-sm shadow-sm backdrop-blur-xl"
		>
			{#if voice.problem}
				<span class="text-destructive">{voice.problem}</span>
			{:else}
				<span
					class={['size-2 shrink-0 rounded-full', voice.phase === 'listening' ? 'animate-pulse bg-red-500' : 'bg-foreground/50']}
				></span>

				<span class="min-w-0 truncate">{text}</span>
			{/if}

			<button
				aria-label={voice.problem ? 'Dismiss' : 'End voice chat'}
				class="flex size-8 shrink-0 items-center justify-center rounded-full hover:bg-foreground/10"
				onclick={() => (voice.problem ? (voice.problem = '') : voice.end())}
				type="button"
			>
				<X class="h-3.5 w-3.5" />
			</button>
		</div>
	</div>
{/if}
