<script lang="ts">
	import type { Snippet } from 'svelte';

	interface Props {
		actions?: Snippet;
		children?: Snippet;
		class?: string;
		description?: string;
		title?: string;
	}

	let { actions, children, class: className = '', description, title }: Props = $props();
</script>

<!-- a glass panel: rises in when the page opens, lifts a little and catches the light on hover -->
<section class={['pk-glass pk-lift pk-sheen pk-rise rounded-[24px] p-4 md:p-6', className]}>
	{#if title || actions}
		<div class="relative mb-3 flex items-start justify-between gap-3">
			<div class="min-w-0">
				{#if title}<h2 class="text-sm font-semibold">{title}</h2>{/if}
				{#if description}<p class="mt-1 text-sm text-muted-foreground">{description}</p>{/if}
			</div>

			{#if actions}<div class="flex shrink-0 items-center gap-2">{@render actions()}</div>{/if}
		</div>
	{/if}

	{@render children?.()}
</section>
