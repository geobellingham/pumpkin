<script lang="ts">
	import { ArrowRight, CircleCheck, EyeOff, Undo2 } from '@lucide/svelte';
	import { Button } from '$lib/components/ui/button';
	import { api, pumpkin } from '../store.svelte';
	import type { ChecklistStep } from '../types';
	import { onMount } from 'svelte';

	let { onhide }: { onhide?: () => void } = $props();

	let steps = $state<ChecklistStep[] | null>(null);
	let showSkipped = $state(false);

	async function load() {
		steps = (await pumpkin.run(() => api<{ steps: ChecklistStep[] }>('checklist')))?.steps ?? [];
	}

	onMount(load);

	// something got set up elsewhere (a model, the image engine, a folder): check the list again
	let seen = '';

	$effect(() => {
		const s = pumpkin.state;
		const key = `${s?.has_server}|${s?.has_models}|${s?.installs.map((i) => i.state).join(',')}|${s?.remote}|${s?.server.preset?.id ?? ''}`;

		if (seen && key !== seen) void load();

		seen = key;
	});

	async function skip(s: ChecklistStep, skipped: boolean) {
		steps = (await pumpkin.run(() => api<{ steps: ChecklistStep[] }>('checklist/skip', { body: { id: s.id, skipped } })))?.steps ?? steps;
	}

	const todo = $derived((steps ?? []).filter((s) => !s.done && !s.skipped));
	const skipped = $derived((steps ?? []).filter((s) => s.skipped));
	const done = $derived((steps ?? []).filter((s) => s.done).length);
	const total = $derived(steps?.length ?? 0);
	const left = $derived(todo.length);
</script>

{#if steps && (left > 0 || skipped.length)}
	<section class="rounded-2xl border border-border/60 p-4 md:p-5">
		<div class="flex items-start justify-between gap-3">
			<div class="min-w-0">
				<h2 class="text-base font-semibold">{left ? 'Set up Pumpkin' : 'Setup is done'}</h2>
				<p class="mt-0.5 text-sm text-muted-foreground">
					{done} of {total} done. {left ? 'Skip anything you do not need.' : 'You skipped a few. Bring them back any time.'}
				</p>
			</div>

			{#if onhide}
				<Button class="-mr-2 rounded-full text-muted-foreground" onclick={onhide} size="sm" variant="ghost">
					<EyeOff class="size-4" /> Hide
				</Button>
			{/if}
		</div>

		<div aria-hidden="true" class="mt-3 flex gap-1">
			{#each steps as s (s.id)}
				<span class={['h-1 flex-1 rounded-full', s.done ? 'bg-emerald-500' : s.skipped ? 'bg-foreground/25' : 'bg-foreground/10']}></span>
			{/each}
		</div>

		<ul class="mt-2 grid lg:grid-cols-2 lg:gap-x-10">
			{#each todo as s (s.id)}
				<li class="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-border/60 py-3">
					<div class="min-w-0 flex-1 basis-56">
						<div class="text-sm font-medium">{s.title}</div>
						<div class="text-xs text-muted-foreground">{s.text}</div>
					</div>
					<div class="flex shrink-0 items-center gap-1">
						{#if s.optional}
							<Button class="rounded-full text-muted-foreground" onclick={() => skip(s, true)} size="sm" variant="ghost">Skip</Button>
						{/if}
						<Button class="rounded-full" href={s.href} size="sm" variant="secondary">Set up <ArrowRight class="size-3.5" /></Button>
					</div>
				</li>
			{/each}
		</ul>

		{#if skipped.length}
			<button class="mt-1 text-xs text-muted-foreground underline underline-offset-2" onclick={() => (showSkipped = !showSkipped)} type="button">
				{showSkipped ? 'Hide' : 'Show'} {skipped.length} skipped
			</button>

			{#if showSkipped}
				<ul class="mt-2 flex flex-col gap-1">
					{#each skipped as s (s.id)}
						<li class="flex items-center gap-2 text-sm text-muted-foreground">
							<CircleCheck class="size-4 shrink-0 opacity-30" />
							<span class="min-w-0 flex-1 truncate">{s.title}</span>
							<Button class="h-8 rounded-full" onclick={() => skip(s, false)} size="sm" variant="ghost"><Undo2 class="size-3.5" /> Bring back</Button>
						</li>
					{/each}
				</ul>
			{/if}
		{/if}
	</section>
{/if}
