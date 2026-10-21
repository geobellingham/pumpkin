<script lang="ts">
	import { Bot, CircleCheck, Code, Cpu, LoaderCircle, MemoryStick, MessageCircle, Search, Sparkles } from '@lucide/svelte';
	import { Button } from '$lib/components/ui/button';
	import Card from '../components/Card.svelte';
	import FitBadge from '../components/FitBadge.svelte';
	import FoundModels from '../components/FoundModels.svelte';
	import PumpkinLogo from '../components/PumpkinLogo.svelte';
	import { api, pumpkin } from '../store.svelte';
	import type { Fit, Hardware, LocalModel } from '../types';
	import LlamaCard from './LlamaCard.svelte';
	import { onMount } from 'svelte';

	interface Pick {
		blurb: string;
		fit: Fit;
		id: string;
		name: string;
		quant: string;
		route: 'gpu' | 'moe';
		size_gb: number;
	}

	const GOALS = [
		{ id: 'chat', icon: MessageCircle, title: 'Chat and everyday questions', text: 'Ask anything, get help writing, learn things.' },
		{ id: 'coding', icon: Code, title: 'Coding', text: 'Write, explain and fix code.' },
		{ id: 'research', icon: Search, title: 'Research with web search', text: 'Looks things up, reads the pages and gives sources.' },
		{ id: 'agent', icon: Bot, title: 'Agents and scheduled tasks', text: 'Runs jobs on its own, like a morning briefing.' }
	];

	let hw = $state<Hardware | null>(null);
	let step = $state(1);
	let goal = $state('chat');
	let picks = $state<Pick[]>([]);
	let loadingPicks = $state(false);
	let mine = $state<LocalModel | null>(null);
	let working = $state(false);

	const s = $derived(pumpkin.state);
	const installing = $derived(s?.installs.find((i) => i.state === 'running'));
	const ready = $derived(s?.server.state === 'ready');
	const labels = ['Your computer', 'What for', 'Model server', 'Your model'];

	onMount(async () => {
		hw = await pumpkin.run(() => api<Hardware>('hardware?fresh=1'));
	});

	async function chooseGoal(id: string) {
		goal = id;
		await pumpkin.run(() => api('setup/goal', { body: { goal: id } }));
		step = pumpkin.state?.has_server ? 4 : 3;

		if (step === 4) void loadPicks();
	}

	async function loadPicks() {
		loadingPicks = true;

		const r = await pumpkin.run(() => api<{ gpu: Pick[]; moe: Pick[] }>(`recommend?goal=${goal}`));
		const m = await api<{ models: LocalModel[] }>('models').catch(() => null);

		picks = r ? [...r.gpu.slice(0, 2), ...r.moe.slice(0, 1)] : [];
		mine =
			(m?.models ?? [])
				.filter((x) => x.kind === 'model' && x.complete && ['gpu', 'moe'].includes(x.fit?.level ?? ''))
				.sort((a, b) => b.size_gb - a.size_gb)[0] ?? null;
		loadingPicks = false;
	}

	async function install(p: Pick) {
		working = true;
		await pumpkin.run(() => api(`catalog/${encodeURIComponent(p.id)}`, { body: { start: true } }), `Installing ${p.name}`);
		working = false;
	}

	async function useMine(m: LocalModel) {
		working = true;

		const p = await pumpkin.run(() => api<{ id: string }>('presets/from-model', { body: { path: m.path } }));

		if (p) await pumpkin.run(() => api('llama/start', { body: { preset: p.id } }));

		working = false;
	}

	async function finish(toChat = true) {
		await pumpkin.run(() => api('settings', { body: { setup_done: true }, method: 'PUT' }));
		location.hash = toChat ? '#/' : '#/pumpkin/overview';
	}
</script>

<div class="flex flex-col gap-5">
	<div class="flex flex-col items-center gap-2 pt-2 text-center">
		<PumpkinLogo class="size-14" />
		<h2 class="text-2xl font-semibold tracking-tight">Welcome to Pumpkin</h2>
		<p class="max-w-md text-sm text-muted-foreground">
			Run AI models and agents on your own computer. Nothing leaves it unless you turn on phone access.
		</p>
	</div>

	<ol aria-label="Setup steps" class="flex flex-wrap justify-center gap-2 text-xs text-muted-foreground">
		{#each labels as label, i (label)}
			<li class={['rounded-full px-3 py-1', step === i + 1 ? 'bg-foreground text-background' : 'bg-muted/60']}>{i + 1}. {label}</li>
		{/each}
	</ol>

	{#if step === 1}
		<Card title="Your computer">
			{#if !hw}
				<div class="flex justify-center py-6"><LoaderCircle class="h-5 w-5 animate-spin text-muted-foreground" /></div>
			{:else}
				<div class="flex flex-col gap-3 text-sm">
					<div class="flex gap-3">
						<MemoryStick class="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
						<div>
							{#if hw.main_gpu}
								<div class="font-medium">{hw.main_gpu.name}</div>
								<div class="text-muted-foreground">{hw.main_gpu.phone ? `Models run on the processor. About ${hw.main_gpu.vram_total.toFixed(0)} GB of the phone's ${hw.ram_total.toFixed(0)} GB memory is free for them` : hw.main_gpu.unified ? `Shares ${hw.ram_total.toFixed(0)} GB of memory with the CPU and can use about ${hw.main_gpu.vram_total.toFixed(0)} GB of it` : hw.main_gpu.vram_total ? `${hw.main_gpu.vram_total.toFixed(0)} GB of video memory` : 'Shares system memory'}</div>
							{:else}
								<div class="font-medium">No graphics card found</div>
								<div class="text-muted-foreground">Models will run on the CPU. Small models still work.</div>
							{/if}
						</div>
					</div>
					<div class="flex gap-3">
						<Cpu class="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
						<div>
							<div class="font-medium">{hw.cpu.model}</div>
							<div class="text-muted-foreground">{hw.cpu.physical_cores} cores, {hw.ram_total.toFixed(0)} GB RAM{hw.disk ? `, ${hw.disk.toFixed(0)} GB free disk` : ''}</div>
						</div>
					</div>
				</div>
			{/if}
		</Card>

		<FoundModels />

		{#if hw}
			<div class="flex justify-end"><Button class="rounded-full" onclick={() => (step = 2)}>Continue</Button></div>
		{/if}
	{:else if step === 2}
		<Card description="Pumpkin uses this to pick a model and a starting personality. You can change both later." title="What will you use it for?">
			<div class="grid gap-3 sm:grid-cols-2">
				{#each GOALS as g (g.id)}
					<button
						class="flex items-start gap-3 rounded-xl border border-border/60 p-3.5 text-left transition-colors hover:bg-muted/50 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
						onclick={() => chooseGoal(g.id)}
						type="button"
					>
						<g.icon class="mt-0.5 h-5 w-5 shrink-0 text-orange-600" />
						<span>
							<span class="block text-sm font-medium">{g.title}</span>
							<span class="block text-xs text-muted-foreground">{g.text}</span>
						</span>
					</button>
				{/each}
			</div>
		</Card>
		<div class="flex justify-start"><Button class="rounded-full" onclick={() => (step = 1)} variant="ghost">Back</Button></div>
	{:else if step === 3}
		<LlamaCard compact ondone={() => { step = 4; void loadPicks(); }} />
		<div class="flex justify-between">
			<Button class="rounded-full" onclick={() => (step = 2)} variant="ghost">Back</Button>
			<Button class="rounded-full" disabled={!s?.has_server} onclick={() => { step = 4; void loadPicks(); }}>Continue</Button>
		</div>
	{:else}
		{#if ready}
			<Card>
				<div class="flex flex-col items-center gap-3 py-4 text-center">
					<CircleCheck class="h-8 w-8 text-emerald-500" />
					<div>
						<div class="font-semibold">{s?.server.preset?.name ?? 'Your model'} is ready</div>
						<p class="text-sm text-muted-foreground">Open the chat and say hello.</p>
					</div>
					<Button class="rounded-full" onclick={() => finish(true)}>Open the chat</Button>
				</div>
			</Card>
		{:else if installing || s?.server.state === 'loading'}
			<Card title={installing ? `Installing ${installing.model}` : 'Starting the model'}>
				<div class="flex flex-col gap-2 text-sm">
					<div class="flex items-center gap-2 text-muted-foreground">
						<LoaderCircle class="h-4 w-4 animate-spin" />
						{installing?.phase ?? 'Loading the model into memory'}
					</div>
					{#if installing?.total}
						<div class="h-2 overflow-hidden rounded-full bg-muted">
							<div class="h-full rounded-full bg-orange-500 transition-all" style="width: {Math.round((installing.done / installing.total) * 100)}%"></div>
						</div>
					{/if}
				</div>
			</Card>
		{:else}
			{#if loadingPicks}
				<div class="flex justify-center py-8"><LoaderCircle class="h-5 w-5 animate-spin text-muted-foreground" /></div>
			{:else}
				{#if mine}
					<Card description="You already have a model that fits this computer." title="Use what you have">
						<div class="flex items-center gap-3">
							<div class="min-w-0 flex-1">
								<div class="truncate text-sm font-medium">{mine.file}</div>
								<div class="text-xs text-muted-foreground">{mine.size_gb} GB</div>
							</div>
							<FitBadge fit={mine.fit} short />
							<Button class="rounded-full" disabled={working} onclick={() => useMine(mine!)} size="sm">Use it</Button>
						</div>
					</Card>
				{/if}

				<Card description="Chosen for your computer and what you want to do." title={mine ? 'Or install one' : 'Pick a model'}>
					<div class="flex flex-col divide-y divide-border/60">
						{#each picks as p, i (p.id)}
							<div class="flex items-center gap-3 py-3">
								<div class="min-w-0 flex-1">
									<div class="flex items-center gap-2 text-sm font-medium">
										<span class="truncate">{p.name}</span>
										{#if i === 0}<span class="shrink-0 rounded-full bg-orange-100 px-2 py-0.5 text-[11px] font-medium text-orange-800 dark:bg-orange-950 dark:text-orange-300">Best match</span>{/if}
									</div>
									<div class="truncate text-xs text-muted-foreground">{p.blurb}</div>
									<div class="mt-1 flex items-center gap-2"><FitBadge fit={p.fit} short /><span class="text-xs text-muted-foreground">{p.quant} · about {p.size_gb} GB</span></div>
								</div>
								<Button class="rounded-full" disabled={working} onclick={() => install(p)} size="sm" variant={i === 0 ? 'default' : 'secondary'}>
									<Sparkles class="h-3.5 w-3.5" /> Install
								</Button>
							</div>
						{:else}
							<p class="py-3 text-sm text-muted-foreground">No recommendation right now. You can search all models on the Models page.</p>
						{/each}
					</div>
				</Card>
			{/if}

			<div class="flex justify-between">
				<Button class="rounded-full" onclick={() => (step = 2)} variant="ghost">Back</Button>
				<Button class="rounded-full" onclick={() => finish(false)} variant="ghost">Skip for now</Button>
			</div>
		{/if}
	{/if}
</div>
