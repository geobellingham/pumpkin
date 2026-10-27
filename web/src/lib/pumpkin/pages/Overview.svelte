<script lang="ts">
	import { CircleAlert, ListChecks, LoaderCircle, MessageSquareText, Play, RefreshCw } from '@lucide/svelte';
	import { Button } from '$lib/components/ui/button';
	import Activity from '../components/Activity.svelte';
	import Card from '../components/Card.svelte';
	import Checklist from '../components/Checklist.svelte';
	import Meter from '../components/Meter.svelte';
	import Progress from '../components/Progress.svelte';
	import Sparkline from '../components/Sparkline.svelte';
	import StatusDot from '../components/StatusDot.svelte';
	import { useSpeed } from '../speed.svelte';
	import { api, pumpkin } from '../store.svelte';
	import type { Preset, Settings } from '../types';
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
	let checklistHidden = $state<boolean | null>(null);

	onMount(async () => {
		checklistHidden = (await api<Settings>('settings').catch(() => null))?.checklist_hidden ?? false;
	});

	async function setHidden(v: boolean) {
		checklistHidden = v;
		await pumpkin.run(() => api('settings', { body: { checklist_hidden: v }, method: 'PUT' }));
	}

	// "too old for this model": download the newest llama.cpp and start the same preset again
	let updating = $state(false);

	async function updateAndRetry() {
		const preset = s?.server.preset?.id;

		updating = true;

		const job = await pumpkin.run(() => api<{ id: string }>('llama/update', { body: { then_start: preset } }));

		while (job) {
			await new Promise((r) => setTimeout(r, 1000));

			const j = await api<{ error: string | null; state: string }>(`jobs/${job.id}`).catch(() => null);

			if (!j || j.state !== 'running') {
				if (j?.state === 'error') pumpkin.say(j.error ?? 'The update did not work', 'error');

				break;
			}
		}

		updating = false;
		void pumpkin.refresh();
	}

	const hist = $derived(pumpkin.history);
	const live = $derived(s?.live);
	const gpuMemLabel = $derived(s?.platform === 'android' ? 'For models' : live?.unified ? 'GPU memory' : 'Video memory');

	function span(sec: number | null | undefined): string {
		if (sec == null) return '–';

		const m = Math.floor(sec / 60);

		if (m < 60) return `${m} min`;

		const h = Math.floor(m / 60);

		return h < 48 ? `${h} h ${m % 60} min` : `${Math.floor(h / 24)} days`;
	}

	function count(n?: number | null): string {
		if (!n) return '–';

		return n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(1)}k` : String(Math.round(n));
	}
</script>

<div class="flex flex-col gap-4">
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

	{#if s?.llama_update?.available}
		<Card description="New models often need a newer llama.cpp. Yours is {s.llama_update.current}." title="llama.cpp {s.llama_update.latest} is out">
			<Button class="rounded-full" href="#/pumpkin/settings" size="sm">Update in Settings</Button>
		</Card>
	{/if}

	{#if s?.migrated?.length}
		<Card description="Brought over from LLM Studio: {s.migrated.join(', ')}." title="Welcome to Pumpkin" />
	{/if}

	<div class="grid gap-4 lg:grid-cols-3">
		<section class="rounded-2xl bg-muted/50 p-4 md:p-5 lg:col-span-2">
			<div class="flex flex-wrap items-start justify-between gap-x-3 gap-y-1">
				<div class="min-w-0">
					<h2 class="truncate text-lg leading-tight font-semibold">{pumpkin.modelName ?? 'No model running'}</h2>
					<p class="mt-0.5 truncate text-sm text-muted-foreground">
						{#if s?.server.preset}
							{Math.round(s.server.preset.ctx / 1024)}k context{s.server.preset.tools ? ', tools' : ''}{s.server.preset.web ? ', web search' : ''}{s.server.preset.mmproj ? ', sees pictures' : ''}
						{:else}
							Pick a preset below to start one.
						{/if}
					</p>
				</div>

				<div class="flex shrink-0 items-center gap-1.5 pt-1 text-xs font-medium">
					<StatusDot />
					{statusText}
				</div>
			</div>

			<div class="mt-4 grid items-end gap-x-6 gap-y-3 sm:grid-cols-[auto_minmax(0,1fr)]">
				<div class="flex items-baseline gap-1.5">
					<span class={['text-5xl leading-none font-semibold tracking-tight tabular-nums', speed.value == null && 'text-muted-foreground/50']}>
						{speed.value != null ? speed.value.toFixed(1) : '0.0'}
					</span>
					<span class="text-sm text-muted-foreground">tokens a second</span>
				</div>
				<div class="min-w-0">
					<Sparkline label="Speed over the last few minutes" values={hist.map((h) => h.speed)} />
				</div>
			</div>

			<dl class="mt-4 grid grid-cols-2 gap-x-4 gap-y-3 border-t border-border/60 pt-4 text-sm sm:grid-cols-4">
				<div>
					<dt class="text-xs text-muted-foreground">Reading speed</dt>
					<dd class="font-medium tabular-nums">{s?.metrics.prompt_tokens_seconds ? `${Math.round(s.metrics.prompt_tokens_seconds)} a second` : '–'}</dd>
				</div>
				<div>
					<dt class="text-xs text-muted-foreground">Written so far</dt>
					<dd class="font-medium tabular-nums">{count(s?.metrics.tokens_predicted_total)}</dd>
				</div>
				<div>
					<dt class="text-xs text-muted-foreground">Longest chat held</dt>
					<dd class="font-medium tabular-nums">{count(s?.metrics.n_tokens_max)}</dd>
				</div>
				<div>
					<dt class="text-xs text-muted-foreground">Running for</dt>
					<dd class="font-medium tabular-nums">{span(s?.server.uptime)}</dd>
				</div>
			</dl>

			{#if s?.server.state === 'error' && s.server.error}
				<div class="mt-4 rounded-xl bg-destructive/10 p-3 text-sm text-destructive">
					<p class="flex flex-wrap gap-2">
						<CircleAlert class="mt-0.5 h-4 w-4 shrink-0" />
						<span class="min-w-0 flex-1 break-words">{s.server.error}</span>
						<a class="shrink-0 underline" href="#/pumpkin/settings">See log</a>
					</p>
					{#if s.server.fix === 'update_llama'}
						<div class="mt-3 flex flex-wrap items-center gap-2">
							<Button class="rounded-full" disabled={updating} onclick={updateAndRetry} size="sm">
								{#if updating}<LoaderCircle class="h-4 w-4 animate-spin" />{:else}<RefreshCw class="h-4 w-4" />{/if}
								Update llama.cpp and try again
							</Button>
							{#if updating}<span class="text-xs text-muted-foreground">Downloading the newest llama.cpp…</span>{/if}
						</div>
					{/if}
				</div>
			{:else if s?.memory_note}
				<p class="mt-4 flex gap-2 rounded-xl bg-amber-500/10 p-3 text-sm text-amber-700 dark:text-amber-400">
					<CircleAlert class="mt-0.5 h-4 w-4 shrink-0" />
					<span class="min-w-0 flex-1">{s.memory_note.text}</span>
				</p>
			{:else if s?.server.notice && (s.server.state === 'ready' || s.server.state === 'loading')}
				<p class="mt-4 rounded-xl bg-background/60 p-3 text-sm text-muted-foreground">{s.server.notice}</p>
			{/if}

			{#if s?.server.state === 'ready' || s?.server.state === 'loading'}
				<div class="mt-4 flex flex-wrap gap-2">
					<Button class="rounded-full" href="#/" size="sm">
						<MessageSquareText class="h-4 w-4" /> Chat
					</Button>
					<Button class="rounded-full" onclick={() => pumpkin.stopModel()} size="sm" variant="outline">
						Stop model
					</Button>
				</div>
			{/if}
		</section>

		<Card title="Memory">
			<div class="flex flex-col gap-4">
				<div class="flex flex-col gap-1.5">
					<Meter label={gpuMemLabel} total={live?.vram_total} used={live?.vram_used} />
					{#if live?.vram_total}<Sparkline class="h-6" label="Video memory over the last few minutes" max={live.vram_total} values={hist.map((h) => h.vram)} />{/if}
				</div>
				<div class="flex flex-col gap-1.5">
					<Meter cached={live?.model_ram} label="RAM" total={live?.ram_total} used={live?.ram_used} />
					{#if live?.ram_total}<Sparkline class="h-6" label="RAM over the last few minutes" max={live.ram_total} values={hist.map((h) => h.ram)} />{/if}
				</div>
				{#if live?.gpu_busy != null}
					<div class="flex items-baseline justify-between border-t border-border/60 pt-3 text-xs text-muted-foreground">
						<span>Graphics card busy</span>
						<span class="text-sm font-medium text-foreground tabular-nums">{Math.round(live.gpu_busy)}%</span>
					</div>
				{/if}
			</div>
		</Card>
	</div>

	<Card description="Everything Pumpkin is working on, live." title="Right now">
		<Activity />
	</Card>

	{#if checklistHidden === false}
		<Checklist onhide={() => setHidden(true)} />
	{:else if checklistHidden}
		<button class="flex items-center gap-2 self-start text-xs text-muted-foreground underline underline-offset-2" onclick={() => setHidden(false)} type="button">
			<ListChecks class="size-4" /> Show the setup list
		</button>
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
