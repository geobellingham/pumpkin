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

<section class={['rounded-2xl border border-border/60 p-4 md:p-5', className]}>
	{#if title || actions}
		<div class="mb-3 flex items-start justify-between gap-3">
			<div class="min-w-0">
				{#if title}<h2 class="text-base font-semibold">{title}</h2>{/if}
				{#if description}<p class="mt-0.5 text-sm text-muted-foreground">{description}</p>{/if}
			</div>

			{#if actions}<div class="flex shrink-0 items-center gap-2">{@render actions()}</div>{/if}
		</div>
	{/if}

	{@render children?.()}
</section>
