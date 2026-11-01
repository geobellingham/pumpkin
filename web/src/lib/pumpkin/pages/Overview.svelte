<script lang="ts">
	import { CircleAlert, Download, ListChecks, LoaderCircle, Plus, RefreshCw } from '@lucide/svelte';
	import { Button } from '$lib/components/ui/button';
	import Activity from '../components/Activity.svelte';
	import Card from '../components/Card.svelte';
	import Checklist from '../components/Checklist.svelte';
	import Progress from '../components/Progress.svelte';
	import { useSpeed } from '../speed.svelte';
	import { api, pumpkin } from '../store.svelte';
	import type { LocalModel, Preset, Settings } from '../types';
	import { onMount } from 'svelte';

	const speed = useSpeed();
	const s = $derived(pumpkin.state);
	let presets = $state<Preset[] | null>(null);
	let models = $state<LocalModel[]>([]);

	async function load() {
		presets = (await pumpkin.run(() => api<{ items: Preset[] }>('presets')))?.items ?? [];
		models = (await api<{ models: LocalModel[] }>('models').catch(() => null))?.models ?? [];
	}

	onMount(load);

	// a finished install or a model change adds presets: reload the list
	let seen = '';

	$effect(() => {
		const key = `${s?.server.preset?.id ?? ''}|${s?.installs.map((i) => i.state).join(',') ?? ''}`;

		if (seen && key !== seen) void load();

		seen = key;
	});

	const running = $derived(s?.server.preset?.id);
	const up = $derived(s?.server.state === 'ready' || s?.server.state === 'external');
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

	// ---- the greeting

	const hour = new Date().getHours();
	const greeting = hour < 5 ? 'Good night' : hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening';

	const subtitle = $derived.by(() => {
		const name = pumpkin.modelName;

		switch (pumpkin.status) {
			case 'ready':
				return `${name} is loaded, everything running on this computer.`;
			case 'busy':
				return `${name} is working on “${s?.agent.job}”.`;
			case 'loading':
				return `Loading ${name ?? 'the model'}…`;
			case 'error':
				return 'The model stopped. The details are below.';
			case 'offline':
				return "Can't reach Pumpkin right now.";
			default:
				return 'No model running. Start one from your library.';
		}
	});

	// ---- the active model

	const preset = $derived(s?.server.preset ?? null);
	const modelOf = (p: Preset | null) => (p ? models.find((m) => m.path === p.model) : undefined);
	const activeModel = $derived(modelOf(preset));

	function quantOf(p: Preset): string | null {
		const m = modelOf(p);

		if (m?.quant) return m.quant;

		return p.model.match(/(?:^|[-_.])((?:I?Q\d+_[A-Z0-9_]+|Q\d+_\d|BF16|F16|F32|MXFP4))(?:[-_.]|$)/i)?.[1]?.toUpperCase() ?? null;
	}

	function sizeOf(p: Preset): string | null {
		const m = modelOf(p);

		return m?.size_gb ? `${m.size_gb.toFixed(1)} GB` : null;
	}

	function k(n?: number | null): string {
		if (!n) return '–';

		return n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(1)}k` : String(Math.round(n));
	}

	function span(sec: number | null | undefined): string {
		if (sec == null) return '–';

		const m = Math.floor(sec / 60);

		if (m < 60) return `${m} min`;

		const h = Math.floor(m / 60);

		return h < 48 ? `${h} h ${m % 60} min` : `${Math.floor(h / 24)} days`;
	}

	// the last 40 speed samples as bars; older ones are dimmer
	const BARS = 40;
	const bars = $derived.by(() => {
		const vals = hist.map((h) => h.speed ?? 0).slice(-BARS);
		const pad = Array(Math.max(0, BARS - vals.length)).fill(0);
		const all = [...pad, ...vals];
		const max = Math.max(1, ...all) * 1.15;

		return all.map((v, i) => ({ h: Math.max(4, Math.round((v / max) * 100)), o: (0.35 + (i / BARS) * 0.65).toFixed(2) }));
	});

	// ---- resources

	const RING = 2 * Math.PI * 52;
	const ringUsed = $derived(live?.vram_total ? live.vram_used ?? 0 : live?.ram_used ?? 0);
	const ringTotal = $derived(live?.vram_total || live?.ram_total || 0);
	const ringOff = $derived(ringTotal ? RING * (1 - Math.min(1, ringUsed / ringTotal)) : RING);
	const ringLabel = $derived(
		s?.platform === 'android' ? 'for models' : live?.vram_total ? (live.unified ? 'GPU memory' : 'VRAM') : 'RAM'
	);

	const meters = $derived.by(() => {
		const out: { label: string; val: string; w: number; bg: string }[] = [];

		if (live?.gpu_busy != null) {
			out.push({ label: 'GPU load', val: `${Math.round(live.gpu_busy)}%`, w: live.gpu_busy, bg: 'linear-gradient(90deg,#ee8548,#f4c3a0)' });
		}

		if (live?.ram_total) {
			out.push({
				label: 'System RAM',
				val: `${(live.ram_used ?? 0).toFixed(1)} / ${live.ram_total.toFixed(0)} GB`,
				w: ((live.ram_used ?? 0) / live.ram_total) * 100,
				bg: 'linear-gradient(90deg,#6d7df0,#b9c8ff)'
			});
		}

		if (preset?.ctx) {
			const used = s?.metrics.n_tokens_max ?? 0;

			out.push({
				label: 'Longest chat',
				val: `${k(used)} / ${k(preset.ctx)}`,
				w: Math.min(100, (used / preset.ctx) * 100),
				bg: 'linear-gradient(90deg,#2fbf8f,#a8f0d4)'
			});
		}

		return out;
	});

</script>

<div class="flex flex-col gap-4">
	<!-- greeting -->
	<div class="pk-rise flex flex-wrap items-end justify-between gap-3 px-1.5 pt-1">
		<div class="min-w-0">
			<h1 class="text-[30px] leading-tight font-semibold tracking-[-0.025em]">{greeting}</h1>
			<p class="mt-1 text-[13px] text-muted-foreground">{subtitle}</p>
		</div>
		<div class="flex gap-2">
			<Button class="h-[38px] px-4 text-[13px] font-semibold" href="#/pumpkin/models" variant="secondary">
				<Download class="size-[15px]" /> Get models
			</Button>
			<Button class="h-[38px] px-4 text-[13px] font-semibold" href="#/">
				<Plus class="size-[15px]" /> New chat
			</Button>
		</div>
	</div>

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
			<Button href="#/pumpkin/settings" size="sm">See the update</Button>
		</Card>
	{/if}

	{#if s?.llama_update?.available}
		<Card description="New models often need a newer llama.cpp. Yours is {s.llama_update.current}." title="llama.cpp {s.llama_update.latest} is out">
			<Button href="#/pumpkin/settings" size="sm">Update in Settings</Button>
		</Card>
	{/if}

	{#if s?.migrated?.length}
		<Card description="Brought over from LLM Studio: {s.migrated.join(', ')}." title="Welcome to Pumpkin" />
	{/if}

	<div class="flex flex-wrap gap-4">
		<!-- the active model -->
		<section aria-label="Active model" class="pk-glass pk-lift pk-sheen pk-rise min-w-0 flex-[1.7_1_440px] rounded-[24px] p-[22px]">
			<div class="relative flex flex-wrap items-start justify-between gap-3">
				<div class="min-w-0">
					<div class="flex items-center gap-2 text-xs text-muted-foreground">
						{#if up}<span aria-hidden="true" class="pk-live"></span>{:else if pumpkin.status === 'loading'}<LoaderCircle class="size-3.5 animate-spin" />{:else}<span aria-hidden="true" class="size-2 rounded-full bg-muted-foreground/50"></span>{/if}
						{up ? 'Active model' : pumpkin.status === 'loading' ? 'Loading' : pumpkin.status === 'error' ? 'Stopped' : 'Nothing loaded'}
					</div>
					<h2 class="mt-2 truncate text-[22px] font-semibold tracking-[-0.015em]">{pumpkin.modelName ?? 'No model running'}</h2>
					{#if preset}
						<div class="mt-1.5 flex flex-wrap gap-1.5">
							{#if quantOf(preset)}<span class="pk-chip pk-chip-hot">{quantOf(preset)}</span>{/if}
							{#if activeModel?.size_gb}<span class="pk-chip">{activeModel.size_gb.toFixed(1)} GB</span>{/if}
							<span class="pk-chip">ctx {k(preset.ctx)}</span>
							{#if preset.tools}<span class="pk-chip">tools</span>{/if}
							{#if preset.web}<span class="pk-chip">web</span>{/if}
							{#if preset.mmproj}<span class="pk-chip">vision</span>{/if}
						</div>
					{:else}
						<p class="mt-1.5 text-[13px] text-muted-foreground">Pick a preset from the library to start one.</p>
					{/if}
				</div>
				<div class="text-right">
					<div class={['text-[44px] leading-none font-semibold tracking-[-0.03em] tabular-nums', speed.value == null && 'text-muted-foreground/50']}>
						{speed.value != null ? Math.round(speed.value) : 0}
					</div>
					<div class="mt-1 text-xs text-muted-foreground">tokens / sec</div>
				</div>
			</div>

			<div aria-label="Generation speed over the last few minutes" class="relative mt-5 flex h-24 items-end gap-[3px]" role="img">
				{#each bars as b, i (i)}
					<div class="spark" style="height: {b.h}%; opacity: {up ? b.o : 0.25}"></div>
				{/each}
			</div>

			{#if s?.server.state === 'error' && s.server.error}
				<div class="relative mt-4 rounded-[14px] bg-destructive/10 p-3 text-sm text-destructive">
					<p class="flex flex-wrap gap-2">
						<CircleAlert class="mt-0.5 h-4 w-4 shrink-0" />
						<span class="min-w-0 flex-1 break-words">{s.server.error}</span>
						<a class="shrink-0 underline" href="#/pumpkin/settings">See log</a>
					</p>
					{#if s.server.fix === 'update_llama'}
						<div class="mt-3 flex flex-wrap items-center gap-2">
							<Button disabled={updating} onclick={updateAndRetry} size="sm">
								{#if updating}<LoaderCircle class="h-4 w-4 animate-spin" />{:else}<RefreshCw class="h-4 w-4" />{/if}
								Update llama.cpp and try again
							</Button>
							{#if updating}<span class="text-xs text-muted-foreground">Downloading the newest llama.cpp…</span>{/if}
						</div>
					{/if}
				</div>
			{:else if s?.memory_note}
				<p class="relative mt-4 flex gap-2 rounded-[14px] bg-amber-500/10 p-3 text-sm text-amber-700 dark:text-amber-400">
					<CircleAlert class="mt-0.5 h-4 w-4 shrink-0" />
					<span class="min-w-0 flex-1">{s.memory_note.text}</span>
				</p>
			{:else if s?.server.notice && (s.server.state === 'ready' || s.server.state === 'loading')}
				<p class="relative mt-4 rounded-[14px] bg-[var(--pk-soft)] p-3 text-sm text-muted-foreground">{s.server.notice}</p>
			{/if}

			<div class="relative mt-4 flex flex-wrap items-center justify-between gap-2.5">
				<div class="font-mono text-[11px] text-muted-foreground tabular-nums">
					prompt {s?.metrics.prompt_tokens_seconds ? Math.round(s.metrics.prompt_tokens_seconds).toLocaleString() : '–'} t/s · {k(s?.metrics.tokens_predicted_total)} written · up {span(s?.server.uptime)}
				</div>
				{#if s?.server.state === 'ready' || s?.server.state === 'loading'}
					<div class="flex gap-2">
						<Button class="h-[34px] px-4 text-xs" onclick={() => pumpkin.stopModel()} variant="secondary">Eject</Button>
						<Button class="h-[34px] px-4 text-xs" href="#/pumpkin/settings" variant="secondary">Server logs</Button>
					</div>
				{/if}
			</div>
		</section>

		<!-- resources -->
		<section aria-label="Resources" class="pk-glass pk-lift pk-rise min-w-0 flex-[1_1_280px] rounded-[24px] p-[22px]">
			<h2 class="text-sm font-semibold">Resources</h2>
			<div class="mt-3.5 flex items-center gap-5">
				<div aria-label="{ringLabel} {ringTotal ? Math.round((ringUsed / ringTotal) * 100) : 0}% used" class="relative size-[124px] shrink-0" role="img">
					<svg aria-hidden="true" class="-rotate-90" fill="none" height="124" viewBox="0 0 124 124" width="124">
						<defs>
							<linearGradient id="pk-ring" x1="0" x2="1" y1="0" y2="1">
								<stop offset="0" stop-color="#f4c3a0" />
								<stop offset="1" stop-color="#e8743a" />
							</linearGradient>
						</defs>
						<circle cx="62" cy="62" r="52" stroke="var(--pk-track)" stroke-width="10" />
						<circle class="pk-ring ring-in" cx="62" cy="62" r="52" stroke="url(#pk-ring)" stroke-dasharray={RING} stroke-dashoffset={ringOff} stroke-linecap="round" stroke-width="10" />
					</svg>
					<div class="absolute inset-0 flex flex-col items-center justify-center">
						<span class="text-xl font-semibold tabular-nums">{ringTotal ? `${ringUsed.toFixed(1)} GB` : '–'}</span>
						<span class="mt-0.5 max-w-[78px] text-center text-[10.5px] leading-[1.25] text-muted-foreground">{ringTotal ? `of ${ringTotal.toFixed(0)} GB` : ''}<br />{ringLabel}</span>
					</div>
				</div>
				<div class="flex min-w-0 flex-1 flex-col gap-3.5">
					{#each meters as m (m.label)}
						<div>
							<div class="flex justify-between gap-2 text-xs">
								<span class="text-muted-foreground">{m.label}</span>
								<span class="font-mono text-[11px] tabular-nums">{m.val}</span>
							</div>
							<div class="mt-1.5 h-1.5 rounded-full bg-[var(--pk-track)]">
								<div class="pk-bar h-full rounded-full" style="width: {Math.max(0, Math.min(100, m.w))}%; background: {m.bg}"></div>
							</div>
						</div>
					{:else}
						<p class="text-xs text-muted-foreground">Memory readings show up once Pumpkin can see them.</p>
					{/each}
				</div>
			</div>
		</section>
	</div>

	<div class="flex flex-wrap gap-4">
		<!-- the library: your presets, ready to load -->
		<section aria-label="Library" class="pk-glass pk-lift pk-rise min-w-0 flex-[1.3_1_400px] rounded-[24px] p-[18px]">
			<div class="flex items-center justify-between px-1.5 pb-1.5">
				<h2 class="text-sm font-semibold">Library</h2>
				<a class="text-xs text-muted-foreground hover:text-foreground" href="#/pumpkin/presets">
					{presets ? `${presets.length} preset${presets.length === 1 ? '' : 's'}` : ''} · Edit
				</a>
			</div>

			{#if presets === null}
				<div class="flex justify-center py-6"><LoaderCircle class="h-5 w-5 animate-spin text-muted-foreground" /></div>
			{:else if !presets.length}
				<p class="px-1.5 py-3 text-sm text-muted-foreground">
					Nothing here yet. <a class="underline" href="#/pumpkin/models">Install a model</a> and Pumpkin makes a preset for you.
				</p>
			{:else}
				{#each presets as p (p.id)}
					{@const active = running === p.id}
					{@const loading = active && s?.server.state === 'loading'}
					<div class="pk-row">
						<div aria-hidden="true" class={['pk-tile', active && 'pk-tile-hot']}>{p.name.trim().charAt(0).toUpperCase()}</div>
						<div class="min-w-0 flex-1">
							<div class="truncate text-[13px] font-semibold">{p.name}</div>
							{#if loading}
								<div class="mt-[7px] flex items-center gap-2">
									<div class="pk-load-track flex-1"><div class="pk-load-fill pk-load-busy"></div></div>
									<span class="font-mono text-[11px] text-[color:var(--pk-accent-text)]">loading</span>
								</div>
							{:else}
								<div class="mt-0.5 truncate font-mono text-[11px] text-muted-foreground">
									{[quantOf(p), sizeOf(p), `${k(p.ctx)} ctx`].filter(Boolean).join(' · ')}
								</div>
							{/if}
						</div>
						{#if active && !loading}
							<span class="flex items-center gap-1.5 rounded-full bg-[rgba(62,224,138,0.12)] px-3 py-1.5 text-xs font-medium text-[color:var(--pk-text)]">
								<span aria-hidden="true" class="pk-live"></span> Running
							</span>
						{:else}
							<Button aria-label="Load {p.name}" class="h-8 px-3.5 text-xs" disabled={s?.server.state === 'loading'} onclick={() => pumpkin.startPreset(p.id)} variant="secondary">
								{loading ? 'Loading' : 'Load'}
							</Button>
						{/if}
					</div>
				{/each}
			{/if}
		</section>

		<!-- what Pumpkin is doing right now -->
		<section aria-label="Right now" class="pk-glass pk-lift pk-rise flex min-w-0 flex-[1_1_320px] flex-col rounded-[24px] p-[18px]">
			<div class="flex items-center justify-between px-1">
				<h2 class="text-sm font-semibold">Right now</h2>
				<span class="text-xs text-muted-foreground">live</span>
			</div>
			<div class="mt-3 px-1"><Activity /></div>
		</section>
	</div>

	{#if checklistHidden === false}
		<Checklist onhide={() => setHidden(true)} />
	{:else if checklistHidden}
		<button class="flex items-center gap-2 self-start px-1.5 text-xs text-muted-foreground underline underline-offset-2" onclick={() => setHidden(false)} type="button">
			<ListChecks class="size-4" /> Show the setup list
		</button>
	{/if}
</div>

<style>
	.spark {
		flex: 1;
		min-width: 2px;
		border-radius: 3px 3px 1px 1px;
		background: linear-gradient(180deg, #f4c3a0, #ee8548 70%, rgba(238, 133, 72, 0.4));
		transition:
			height 0.55s var(--pk-smooth),
			opacity 0.55s;
	}

	.ring-in {
		animation: ring-in 1.8s var(--pk-smooth) 0.3s backwards;
	}

	@keyframes ring-in {
		from {
			stroke-dashoffset: 326.73;
		}
	}
</style>
