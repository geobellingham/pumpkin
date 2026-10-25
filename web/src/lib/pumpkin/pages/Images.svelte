<script lang="ts">
	import { Download, ImagePlus, LoaderCircle, RotateCcw, Trash } from '@lucide/svelte';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import Card from '../components/Card.svelte';
	import Empty from '../components/Empty.svelte';
	import Progress from '../components/Progress.svelte';
	import Segmented from '../components/Segmented.svelte';
	import Toggle from '../components/Toggle.svelte';
	import { api, pumpkin } from '../store.svelte';
	import { onDestroy, onMount } from 'svelte';

	interface ImageFit {
		level: 'gpu' | 'partial' | 'slow' | 'no';
		text: string;
	}

	interface Bundle {
		default: boolean;
		kind: 'draw' | 'edit' | 'video';
		desc: string;
		done: number;
		downloading: boolean;
		error: string | null;
		fit: ImageFit | null;
		id: string;
		installed: boolean;
		name: string;
		partial: boolean;
		recommended: boolean;
		size_gb: number | null;
		total: number;
	}

	interface State {
		bundles: Bundle[];
		enabled: boolean;
		engine: { backend: string | null; installed: boolean; job: string | null; recommended: string | null; tag: string | null };
		folder: string;
		keep_min: number;
		ready: boolean;
		server: { busy: boolean; error: string | null; model: string | null; progress: { stage: string; swapped: boolean } | null; state: string } | null;
		supported: boolean;
		swap: string;
		text: { draws?: boolean; name?: string; preset?: string; running: boolean; tools?: boolean };
	}

	interface Picture {
		height: number;
		model: string;
		seconds: number;
		swapped: boolean;
		url: string;
		width: number;
	}

	let st = $state<State | null>(null);
	let enabled = $state(true);
	let swap = $state('auto');
	let engineJob = $state<{ done: number; error: string | null; state: string; total: number } | null>(null);
	let engineJobId: string | null = null;
	let prompt = $state('');
	let shape = $state('square');
	let drawing = $state(false);
	let picture = $state<Picture | null>(null);
	let timer: ReturnType<typeof setInterval> | undefined;

	const BACKEND: Record<string, string> = { cpu: 'the processor', cuda12: 'your NVIDIA card (CUDA)', metal: 'the Mac GPU (Metal)', vulkan: 'your graphics card (Vulkan)' };

	async function load() {
		const r = await api<State>('images').catch(() => null);

		if (r) {
			st = r;
			enabled = r.enabled;
			swap = r.swap;
		}

		if (st?.engine.job) {
			engineJobId = st.engine.job;
			engineJob = await api<typeof engineJob>(`jobs/${st.engine.job}`).catch(() => null);
		} else if (engineJob?.state === 'running') {
			// it finished: ask once how, so a failed download shows its reason instead of the Install button alone
			const last = engineJobId ? await api<typeof engineJob>(`jobs/${engineJobId}`).catch(() => null) : null;
			engineJob = last?.state === 'error' ? last : null;
			engineJobId = null;
		}
	}

	onMount(() => {
		void load();
		timer = setInterval(() => {
			if (busy()) void load();
		}, 2000);
	});

	onDestroy(() => clearInterval(timer));

	function busy() {
		return !!(st?.engine.job || st?.bundles.some((b) => b.downloading) || st?.server?.busy || drawing);
	}

	async function setEnabled(v: boolean) {
		await pumpkin.run(() => api('settings', { body: { images: v }, method: 'PUT' }), v ? 'The model can draw' : 'Drawing is off');
		void load();
	}

	async function setSwap(v: string) {
		await pumpkin.run(() => api('settings', { body: { image_swap: v }, method: 'PUT' }), 'Saved');
	}

	async function installEngine() {
		const r = await pumpkin.run(() => api<{ job: string }>('images/engine', { body: {} }));

		if (r) void load();
	}

	async function install(b: Bundle) {
		if (await pumpkin.run(() => api('images/install', { body: { id: b.id } }), `Downloading ${b.name}`)) void load();
	}

	async function remove(b: Bundle) {
		if (!confirm(`Delete ${b.name}? You can download it again later.`)) return;

		if (await pumpkin.run(() => api(`images/${b.id}`, { method: 'DELETE' }), 'Deleted')) void load();
	}

	async function useIt(b: Bundle) {
		if (await pumpkin.run(() => api('settings', { body: { [b.kind === 'edit' ? 'edit_model' : b.kind === 'video' ? 'video_model' : 'image_model']: b.id }, method: 'PUT' }), `Using ${b.name}`)) void load();
	}

	async function restartText() {
		const id = st?.text.preset;

		if (!id) return;

		if (await pumpkin.run(() => api('llama/start', { body: { preset: id } }), 'Restarting the model')) void load();
	}

	async function draw() {
		const text = prompt.trim();

		if (!text || drawing) return;

		drawing = true;
		const r = await pumpkin.run(() => api<Picture>('images/generate', { body: { prompt: text, shape } }));

		drawing = false;

		if (r) picture = r;

		void load();
	}

	const installed = $derived(st?.bundles.filter((b) => b.installed) ?? []);
	const canDraw = $derived(!!st?.ready);
	const stage = $derived(
		st?.server?.progress?.stage === 'loading'
			? st.server.progress.swapped
				? 'The text model is paused. Loading the image model…'
				: 'Loading the image model…'
			: 'Drawing…'
	);

	const FIT_CLASS: Record<string, string> = {
		gpu: 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-400',
		no: 'bg-red-500/15 text-red-700 dark:text-red-400',
		partial: 'bg-sky-500/15 text-sky-700 dark:text-sky-400',
		slow: 'bg-amber-500/15 text-amber-700 dark:text-amber-400'
	};
</script>

<div class="flex flex-col gap-4">
	<Card
		description="The chat's model can draw the pictures you ask for. A separate image engine runs on this computer, and pictures are saved in your Files under images. When the text model and the image model do not fit in memory together, the text model pauses while a picture is drawn and comes back by itself."
		title="Pictures"
	>
		{#if st === null}
			<div class="flex justify-center py-8"><LoaderCircle class="h-5 w-5 animate-spin text-muted-foreground" /></div>
		{:else if !st.supported}
			<Empty text="Pictures need a computer. A paired computer can draw for this device later." />
		{:else}
			<Toggle bind:checked={enabled} description="When it is off, the chat's model does not get the drawing tool." label="Let the model draw" onchange={setEnabled} />

			<div class="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-xl bg-muted/40 p-3">
				<div class="min-w-0">
					<p class="text-sm font-medium">Image engine</p>
					<p class="text-xs text-muted-foreground">
						{#if st.engine.installed}
							Installed{st.engine.backend ? `, runs on ${BACKEND[st.engine.backend] ?? st.engine.backend}` : ''}.
						{:else}
							stable-diffusion.cpp, about 50 to 400 MB{st.engine.recommended ? `, for ${BACKEND[st.engine.recommended] ?? st.engine.recommended}` : ''}.
						{/if}
					</p>
				</div>
				{#if st.engine.job && engineJob}
					<div class="w-48"><Progress done={engineJob.done} label="Downloading" total={engineJob.total} /></div>
				{:else if !st.engine.installed}
					<Button class="rounded-full" onclick={installEngine} size="sm"><Download class="mr-1.5 h-4 w-4" />Install</Button>
				{/if}
			</div>
			{#if engineJob?.state === 'error'}
				<p class="mt-2 text-sm text-red-600 dark:text-red-400">{engineJob.error}</p>
			{/if}

			{#if st.text.running && canDraw && !st.text.draws}
				<div class="mt-3 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-amber-500/30 bg-amber-500/10 p-3">
					<p class="text-sm">
						{st.text.tools
							? `${st.text.name} was started before it could draw. Restart it once to give it the drawing tool.`
							: `${st.text.name} has Tools off, so it cannot draw. Turn on Tools in its preset.`}
					</p>
					{#if st.text.tools}
						<Button class="rounded-full" onclick={restartText} size="sm" variant="outline"><RotateCcw class="mr-1.5 h-4 w-4" />Restart</Button>
					{/if}
				</div>
			{/if}
		{/if}
	</Card>

	{#if st?.supported}
		<Card description="Each one is a picture model with its text encoder, picked for this computer. Pumpkin chooses the quality that fits your graphics card." title="Image models">
			<ul class="flex flex-col divide-y divide-border/60">
				{#each st.bundles as b (b.id)}
					<li class="flex flex-col gap-2 py-3 sm:flex-row sm:items-center">
						<div class="min-w-0 flex-1">
							<div class="flex flex-wrap items-center gap-2">
								<p class="text-sm font-medium">{b.name}</p>
								{#if b.recommended}<span class="rounded-full bg-foreground/10 px-2 py-0.5 text-[11px]">Recommended</span>{/if}
								{#if b.default && b.installed}<span class="rounded-full bg-foreground px-2 py-0.5 text-[11px] text-background">{b.kind === 'edit' ? 'Edits in chat' : b.kind === 'video' ? 'Makes videos in chat' : 'Draws in chat'}</span>{/if}
								{#if b.fit}<span class={['rounded-full px-2 py-0.5 text-[11px] font-medium', FIT_CLASS[b.fit.level]]}>{b.fit.text}</span>{/if}
							</div>
							<p class="mt-0.5 text-xs text-muted-foreground">{b.desc}{b.size_gb ? ` About ${b.size_gb} GB.` : ''}</p>
							{#if b.downloading}
								<div class="mt-2"><Progress done={b.done} label="Downloading" total={b.total} /></div>
							{/if}
							{#if b.error}<p class="mt-1 text-xs text-red-600 dark:text-red-400">{b.error}</p>{/if}
						</div>
						<div class="flex shrink-0 gap-2">
							{#if b.installed}
								{#if !b.default}
									<Button class="rounded-full" onclick={() => useIt(b)} size="sm" variant="outline">Use in chat</Button>
								{/if}
								<Button aria-label="Delete {b.name}" class="size-9 rounded-full" onclick={() => remove(b)} size="icon" variant="ghost"><Trash class="h-4 w-4" /></Button>
							{:else if !b.downloading}
								<Button class="rounded-full" disabled={b.fit?.level === 'no'} onclick={() => install(b)} size="sm" variant={b.recommended ? 'default' : 'outline'}>
									<Download class="mr-1.5 h-4 w-4" />{b.partial || b.error ? 'Resume' : 'Download'}
								</Button>
							{/if}
						</div>
					</li>
				{/each}
			</ul>
			<p class="mt-2 text-xs text-muted-foreground">Saved in {st.folder}</p>
		</Card>

		<Card description="Make a picture here, or just ask for one in a chat with Tools on." title="Try it">
			{#if !canDraw}
				<Empty text={st.engine.installed ? 'Download an image model above first.' : 'Install the image engine and an image model above first.'} />
			{:else}
				<form
					class="flex flex-col gap-3"
					onsubmit={(e) => {
						e.preventDefault();
						void draw();
					}}
				>
					<Input bind:value={prompt} maxlength={2000} placeholder="A lighthouse on a cliff at sunset, oil painting" />
					<div class="flex flex-wrap items-center justify-between gap-3">
						<Segmented
							bind:value={shape}
							options={[
								{ label: 'Square', value: 'square' },
								{ label: 'Portrait', value: 'portrait' },
								{ label: 'Landscape', value: 'landscape' },
								{ label: 'Wide', value: 'wide' }
							]}
						/>
						<Button class="rounded-full" disabled={!prompt.trim() || drawing} type="submit">
							{#if drawing}<LoaderCircle class="mr-1.5 h-4 w-4 animate-spin" />{:else}<ImagePlus class="mr-1.5 h-4 w-4" />{/if}Draw
						</Button>
					</div>
				</form>
				{#if drawing}
					<p class="mt-3 text-sm text-muted-foreground">{stage}</p>
				{/if}
				{#if picture}
					<figure class="mt-4">
						<a href={picture.url} rel="noopener" target="_blank">
							<img alt={prompt} class="max-h-[70vh] w-auto max-w-full rounded-xl border border-border/60" height={picture.height} src={picture.url} width={picture.width} />
						</a>
						<figcaption class="mt-1 text-xs text-muted-foreground">
							{picture.model}, {picture.seconds} s{picture.swapped ? '. The text model paused meanwhile' : ''}.
						</figcaption>
					</figure>
				{/if}
			{/if}
		</Card>

		<Card description="Pausing makes room on the graphics card, and costs a few seconds while the text model loads again." title="Memory">
			<p class="mb-2 text-sm">Pause the text model while drawing</p>
			<Segmented
				bind:value={swap}
				onchange={setSwap}
				options={[
					{ label: 'When needed', value: 'auto' },
					{ label: 'Always', value: 'always' },
					{ label: 'Never', value: 'never' }
				]}
			/>
			{#if installed.length && st.server?.state === 'ready'}
				<p class="mt-3 text-xs text-muted-foreground">{st.server.model} is loaded. It frees its memory after {st.keep_min} idle minutes.</p>
			{/if}
		</Card>
	{/if}
</div>
