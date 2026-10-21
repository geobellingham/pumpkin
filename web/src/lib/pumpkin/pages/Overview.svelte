<script lang="ts">
	import { CircleAlert, CircleCheck, LoaderCircle, MessageSquareText, Play } from '@lucide/svelte';
	import { Button } from '$lib/components/ui/button';
	import Card from '../components/Card.svelte';
	import Meter from '../components/Meter.svelte';
	import Progress from '../components/Progress.svelte';
	import StatusDot from '../components/StatusDot.svelte';
	import { useSpeed } from '../speed.svelte';
	import { api, pumpkin } from '../store.svelte';
	import type { Preset } from '../types';
	import { onMount } from 'svelte';

	const speed = useSpeed();
	const s = $derived(pumpkin.state);
	let presets = $state<Preset[] | null>(null);

	async function load() {
		presets = (await pumpkin.run(() => api<{ items: Preset[] }>('presets')))?.items ?? [];
	}

	onMount(load);

	// a finished install or a model change adds presets: reload the list
	let seen = '';

	$effect(() => {
		const key = `${s?.server.preset?.id ?? ''}|${s?.installs.map((i) => i.state).join(',') ?? ''}`;

		if (seen && key !== seen) void load();

		seen = key;
	});

	const statusText = $derived.by(() => {
		switch (pumpkin.status) {
			case 'ready':
				return 'Ready';
			case 'busy':
				return `Running “${s?.agent.job}”`;
			case 'loading':
				return 'Loading the model';
			case 'error':
				return 'The model stopped';
			case 'offline':
				return "Can't reach Pumpkin";
			default:
				return 'No model running';
		}
	});

	const running = $derived(s?.server.preset?.id);
	const steps = $derived(
		s
			? [
					{ done: s.has_server, href: '#/pumpkin/settings', label: 'Get llama-server, the engine that runs models' },
					{ done: s.has_models, href: '#/pumpkin/models', label: 'Install a model that fits your computer' },
					{ done: !!presets?.length, href: '#/pumpkin/presets', label: 'Have a preset to start it with' }
				]
			: []
	);
	const allDone = $derived(steps.every((x) => x.done));
</script>

<div class="flex flex-col gap-4">
	<section class="rounded-2xl bg-muted/50 p-4 md:p-5">
		<div class="flex items-start justify-between gap-3">
			<div class="min-w-0">
				<div class="flex items-baseline gap-1.5">
					<span class={['text-4xl font-semibold tracking-tight tabular-nums', speed.value == null && 'text-muted-foreground/50']}>
						{speed.value != null ? speed.value.toFixed(1) : '0.0'}
					</span>
					<span class="text-sm text-muted-foreground">tokens / s</span>
				</div>

				<div class="mt-1 truncate text-sm text-muted-foreground">
					{pumpkin.modelName ?? 'Pick a preset below to start a model'}
				</div>
			</div>

			<div class="flex shrink-0 items-center gap-1.5 pt-1 text-xs font-medium">
				<StatusDot />
				{statusText}
			</div>
		</div>

		<div class="mt-4 flex gap-4">
			<Meter label="VRAM" total={s?.live.vram_total} used={s?.live.vram_used} />
			<Meter label="RAM" total={s?.live.ram_total} used={s?.live.ram_used} />
		</div>

		{#if s?.server.state === 'error' && s.server.error}
			<p class="mt-4 flex gap-2 rounded-xl bg-destructive/10 p-3 text-sm text-destructive">
				<CircleAlert class="mt-0.5 h-4 w-4 shrink-0" />
				{s.server.error}
				<a class="ml-auto shrink-0 underline" href="#/pumpkin/settings">See log</a>
			</p>
		{/if}

		<div class="mt-4 flex flex-wrap gap-2">
			{#if s?.server.state === 'ready' || s?.server.state === 'loading'}
				<Button class="rounded-full" href="#/" size="sm">
					<MessageSquareText class="h-4 w-4" /> Chat
				</Button>
				<Button class="rounded-full" onclick={() => pumpkin.stopModel()} size="sm" variant="outline">
					Stop model
				</Button>
			{/if}
		</div>
	</section>

	{#each s?.installs ?? [] as inst (inst.id)}
		<Card title="Installing {inst.model}">
			{#if inst.state === 'error'}
				<p class="text-sm text-destructive">{inst.error}</p>
			{:else if inst.state === 'done'}
				<p class="text-sm text-emerald-600 dark:text-emerald-400">Installed and started. <a class="underline" href="#/">Open the chat</a></p>
			{:else}
				<Progress done={inst.done} label={inst.phase} total={inst.phase.startsWith('Downloading') ? inst.total : 0} />
			{/if}
		</Card>
	{/each}

	{#if s?.update?.available}
		<Card description="Open Settings to see what changed and update." title="Pumpkin {s.update.latest} is out">
			<Button class="rounded-full" href="#/pumpkin/settings" size="sm">See the update</Button>
		</Card>
	{/if}

	{#if s?.migrated?.length}
		<Card description="Brought over from LLM Studio: {s.migrated.join(', ')}." title="Welcome to Pumpkin" />
	{/if}

	{#if !allDone && presets !== null}
		<Card description="Three steps and you can chat with a model running on this computer." title="Get started">
			<ol class="flex flex-col gap-1">
				{#each steps as step, i (i)}
					<li>
						<a class="flex items-center gap-3 rounded-xl px-2 py-2 text-sm hover:bg-muted/60" href={step.href}>
							{#if step.done}
								<CircleCheck class="h-5 w-5 shrink-0 text-emerald-500" />
							{:else}
								<span class="flex size-5 shrink-0 items-center justify-center rounded-full border border-border text-[11px]">{i + 1}</span>
							{/if}
							<span class={step.done ? 'text-muted-foreground line-through' : ''}>{step.label}</span>
						</a>
					</li>
				{/each}
			</ol>
		</Card>
	{/if}

	<Card title="Presets">
		{#snippet actions()}
			<Button class="rounded-full" href="#/pumpkin/presets" size="sm" variant="ghost">Edit</Button>
		{/snippet}

		{#if presets === null}
			<div class="flex justify-center py-6"><LoaderCircle class="h-5 w-5 animate-spin text-muted-foreground" /></div>
		{:else if !presets.length}
			<p class="text-sm text-muted-foreground">
				No presets yet. <a class="underline" href="#/pumpkin/models">Install a model</a> and Pumpkin makes one for you.
			</p>
		{:else}
			<div class="flex flex-col divide-y divide-border/60">
				{#each presets as p (p.id)}
					{@const active = running === p.id}
					<div class="flex items-center gap-3 py-2.5">
						<div class="min-w-0 flex-1">
							<div class="truncate font-medium">{p.name}</div>
							<div class="truncate text-xs text-muted-foreground">
								{Math.round(p.ctx / 1024)}k context{p.tools ? ' · tools' : ''}{p.web ? ' · web search' : ''}
							</div>
						</div>

						{#if active && s?.server.state === 'loading'}
							<span class="flex items-center gap-1.5 text-xs text-muted-foreground"><LoaderCircle class="h-3.5 w-3.5 animate-spin" /> Loading</span>
						{:else if active}
							<span class="rounded-full bg-emerald-500/15 px-2 py-0.5 text-[11px] font-medium text-emerald-700 dark:text-emerald-400">Running</span>
						{:else}
							<Button class="h-9 rounded-full px-4" onclick={() => pumpkin.startPreset(p.id)} size="sm" variant={running ? 'secondary' : 'default'}>
								<Play class="h-3.5 w-3.5" />
								{running ? 'Switch' : 'Start'}
							</Button>
						{/if}
					</div>
				{/each}
			</div>
		{/if}
	</Card>
</div>
