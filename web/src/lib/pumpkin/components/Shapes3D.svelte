<script lang="ts">
	// The Images page's 3D part: install TRELLIS.2 (trellis.cpp), pick the quality, and make a 3D model from a picture
	// or from words (the image model draws the picture first).
	import { Box, Download, ImageUp, LoaderCircle, Square, Trash } from '@lucide/svelte';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import Card from './Card.svelte';
	import Empty from './Empty.svelte';
	import ModelViewer from './ModelViewer.svelte';
	import Progress from './Progress.svelte';
	import Segmented from './Segmented.svelte';
	import Toggle from './Toggle.svelte';
	import { api, pumpkin } from '../store.svelte';
	import { onDestroy, onMount } from 'svelte';

	interface Fit {
		level: string;
		text: string;
	}

	interface State {
		auto: 'fast' | 'detailed' | 'best';
		fits: Record<string, Fit>;
		matte: boolean;
		model: 'auto' | 'trellis' | 'pixal3d';
		pixal3d: { done: number; downloading: boolean; error: string | null; installed: boolean; partial: boolean; size_gb: number; total: number };
		done: number;
		downloading: boolean;
		draws: boolean;
		engine: { installed: boolean; tag: string | null };
		engine_job: string | null;
		error: string | null;
		fit: { level: string; text: string } | null;
		folder: string;
		installed: boolean;
		partial: boolean;
		progress: { detail?: string; family?: string; quality?: string; since: number; stage: string; swapped?: boolean } | null;
		quality: string;
		ready: boolean;
		size_gb: number;
		supported: boolean;
		total: number;
	}

	interface Made {
		fell_back: boolean;
		matte: boolean;
		model: string;
		path: string;
		picture: string | null;
		quality: string;
		seconds: number;
		swapped: boolean;
		url: string;
	}

	let st = $state<State | null>(null);
	let quality = $state('auto');
	let family = $state('pixal3d');
	let matte = $state(false);
	let prompt = $state('');
	let picture = $state<{ data: string; name: string } | null>(null);
	let making = $state(false);
	let made = $state<Made | null>(null);
	let engineJob = $state<{ done: number; error: string | null; state: string; total: number } | null>(null);
	let now = $state(Date.now());
	let timer: ReturnType<typeof setInterval> | undefined;
	let fileInput: HTMLInputElement;

	async function load() {
		const r = await api<State>('shapes').catch(() => null);

		if (r) {
			st = r;
			quality = r.quality;
			family = r.model === 'trellis' ? 'trellis' : 'pixal3d';
			matte = r.matte;
		}

		engineJob = st?.engine_job ? await api<typeof engineJob>(`jobs/${st.engine_job}`).catch(() => null) : engineJob?.state === 'error' ? engineJob : null;
	}

	onMount(() => {
		void load();
		timer = setInterval(() => {
			now = Date.now();

			if (st?.downloading || st?.pixal3d?.downloading || st?.engine_job || st?.progress || making) void load();
		}, 2000);
	});

	onDestroy(() => clearInterval(timer));

	async function install() {
		if (await pumpkin.run(() => api<State>('shapes/install', { body: {} }), 'Downloading the 3D model')) void load();
	}

	async function remove() {
		if (!confirm('Delete the 3D model files (about 16 GB)? You can download them again later.')) return;

		if (await pumpkin.run(() => api('shapes/models', { method: 'DELETE' }), 'Deleted')) void load();
	}

	async function setQuality(v: string) {
		await pumpkin.run(() => api('shapes/settings', { body: { quality: v }, method: 'PUT' }), 'Saved');
	}

	async function setFamily(v: string) {
		await pumpkin.run(() => api('shapes/settings', { body: { model: v === 'trellis' ? 'trellis' : 'auto' }, method: 'PUT' }), 'Saved');
	}

	async function setMatte(v: boolean) {
		await pumpkin.run(() => api('shapes/settings', { body: { matte: v }, method: 'PUT' }), v ? 'Matte surfaces' : 'Surfaces as made');
	}

	async function installPixal() {
		if (await pumpkin.run(() => api<State>('shapes/pixal3d', { body: {} }), 'Downloading Pixal3D')) void load();
	}

	function pick(e: Event) {
		const f = (e.target as HTMLInputElement).files?.[0];

		if (!f) return;

		if (f.size > 30_000_000) {
			pumpkin.say('That picture is over 30 MB.', 'error');

			return;
		}

		const r = new FileReader();

		r.onload = () => (picture = { data: String(r.result), name: f.name });
		r.readAsDataURL(f);
	}

	async function make() {
		if (making || (!picture && !prompt.trim())) return;

		making = true;
		made = null;
		const body: Record<string, unknown> = { prompt: prompt.trim() || picture?.name.replace(/\.[^.]+$/, '') };

		if (picture) body.image = picture.data;

		const r = await pumpkin.run(() => api<Made>('shapes/make', { body }));

		making = false;

		if (r) made = r;

		void load();
	}

	async function stop() {
		await pumpkin.run(() => api('shapes/cancel', { body: {} }), 'Stopping');
	}

	const elapsed = $derived(st?.progress ? Math.max(0, Math.round(now / 1000 - st.progress.since)) : 0);
	const QNAME: Record<string, string> = { best: 'Best', detailed: 'Detailed', fast: 'Fast' };
	const stage = $derived(
		st?.progress?.stage === 'drawing'
			? 'Drawing the picture first…'
			: `Making the 3D model${st?.progress?.swapped ? ' (the text model is paused)' : ''}…`
	);
</script>

<Card
	description="Turn a picture into a textured 3D model you can turn around and download (.glb, opens in Blender, game engines and 3D viewers). From words, the image model draws the picture first. It runs on this computer and takes a few minutes. Best results: one whole object, evenly lit, on a plain background, with space around it."
	title="3D models"
>
	{#if st === null}
		<div class="flex justify-center py-6"><LoaderCircle class="h-5 w-5 animate-spin text-muted-foreground" /></div>
	{:else if !st.supported}
		<Empty text="3D models need Linux or Windows for now. A paired PC can make them for this device." />
	{:else}
		<div class="flex flex-wrap items-center justify-between gap-3 rounded-xl bg-muted/40 p-3">
			<div class="min-w-0">
				<p class="text-sm font-medium">TRELLIS.2</p>
				<p class="text-xs text-muted-foreground">
					{#if st.ready}
						Installed{st.engine.tag ? `, trellis.cpp ${st.engine.tag}` : ''}. Saved in {st.folder}.
					{:else}
						The engine and about {st.size_gb} GB of model files.{st.fit ? ` ${st.fit.text}.` : ''}
					{/if}
				</p>
				{#if st.downloading}
					<div class="mt-2 w-64 max-w-full"><Progress done={st.done} label="Downloading" total={st.total} /></div>
				{:else if st.engine_job && engineJob}
					<div class="mt-2 w-64 max-w-full"><Progress done={engineJob.done} label="Downloading the engine" total={engineJob.total} /></div>
				{/if}
				{#if st.error}<p class="mt-1 text-xs text-red-600 dark:text-red-400">{st.error}</p>{/if}
				{#if engineJob?.state === 'error'}<p class="mt-1 text-xs text-red-600 dark:text-red-400">{engineJob.error}</p>{/if}
			</div>
			{#if st.ready}
				<Button aria-label="Delete the 3D model files" class="size-9 rounded-full" disabled={!!st.progress} onclick={remove} size="icon" variant="ghost"><Trash class="h-4 w-4" /></Button>
			{:else if !st.downloading && !st.engine_job}
				<Button class="rounded-full" onclick={install} size="sm"><Download class="mr-1.5 h-4 w-4" />{st.partial || st.error ? 'Resume' : 'Install'}</Button>
			{/if}
		</div>

		{#if st.ready}
			<div class="mt-4">
				<p class="mb-2 text-sm">Model</p>
				{#if st.pixal3d.installed}
					<Segmented
						bind:value={family}
						onchange={setFamily}
						options={[
							{ label: 'Pixal3D', value: 'pixal3d' },
							{ label: 'TRELLIS.2', value: 'trellis' }
						]}
					/>
					<p class="mt-1 text-xs text-muted-foreground">Pixal3D follows the picture more closely. At Fast it hands over to TRELLIS.2, which has the smaller texture model.</p>
				{:else}
					<div class="flex flex-wrap items-center justify-between gap-3 rounded-xl bg-muted/40 p-3">
						<div class="min-w-0">
							<p class="text-sm font-medium">Pixal3D</p>
							<p class="text-xs text-muted-foreground">A TRELLIS.2 fine-tune whose shape follows the picture much more closely. About {st.pixal3d.size_gb} GB more; it shares the other files.</p>
							{#if st.pixal3d.downloading}
								<div class="mt-2 w-64 max-w-full"><Progress done={st.pixal3d.done} label="Downloading" total={st.pixal3d.total} /></div>
							{/if}
							{#if st.pixal3d.error}<p class="mt-1 text-xs text-red-600 dark:text-red-400">{st.pixal3d.error}</p>{/if}
						</div>
						{#if !st.pixal3d.downloading}
							<Button class="rounded-full" onclick={installPixal} size="sm" variant="outline"><Download class="mr-1.5 h-4 w-4" />{st.pixal3d.partial || st.pixal3d.error ? 'Resume' : 'Add Pixal3D'}</Button>
						{/if}
					</div>
				{/if}
			</div>
		{/if}

		<div class="mt-4">
			<p class="mb-2 text-sm">Quality</p>
			<Segmented
				bind:value={quality}
				onchange={setQuality}
				options={[
					{ label: `Auto (${QNAME[st.auto].toLowerCase()})`, value: 'auto' },
					{ label: 'Fast', value: 'fast' },
					{ label: 'Detailed', value: 'detailed' },
					{ label: 'Best', value: 'best' }
				]}
			/>
			<p class="mt-1 text-xs text-muted-foreground">
				Fast suits smaller cards. Detailed needs about a 16 GB graphics card. Best is finer still with sharper textures, takes the longest, and drops to Detailed by itself if the card runs out of memory.{st.fits?.best ? ` Best here: ${st.fits.best.text.toLowerCase()}.` : ''}
			</p>
		</div>

		<div class="mt-4">
			<Toggle bind:checked={matte} description="TRELLIS often makes fabric and plastic partly metallic, so dark clothes come out looking like black plastic. This takes the metal look off the saved model; shape and colours stay. Leave it off for real metal." label="Matte surfaces" onchange={setMatte} />
		</div>

		{#if st.ready}
			<form
				class="mt-4 flex flex-col gap-3"
				onsubmit={(e) => {
					e.preventDefault();
					void make();
				}}
			>
				<div class="flex flex-wrap items-center gap-2">
					<input accept="image/png,image/jpeg,image/webp" bind:this={fileInput} class="hidden" onchange={pick} type="file" />
					<Button class="rounded-full" onclick={() => fileInput.click()} size="sm" type="button" variant="outline"><ImageUp class="mr-1.5 h-4 w-4" />{picture ? 'Another picture' : 'Pick a picture'}</Button>
					{#if picture}
						<span class="flex items-center gap-2 text-xs text-muted-foreground">
							<img alt="" class="h-9 w-9 rounded-lg object-cover" src={picture.data} />{picture.name}
							<button class="underline" onclick={() => (picture = null)} type="button">Remove</button>
						</span>
					{/if}
				</div>
				<Input bind:value={prompt} maxlength={400} placeholder={picture ? 'A name for it (optional)' : st.draws ? 'Or describe one object: a carved pumpkin lantern' : 'Pick a picture (an image model lets you describe it in words instead)'} />
				<div class="flex flex-wrap items-center gap-2">
					<Button class="rounded-full" disabled={making || !!st.progress || (!picture && (!prompt.trim() || !st.draws))} type="submit">
						{#if making}<LoaderCircle class="mr-1.5 h-4 w-4 animate-spin" />{:else}<Box class="mr-1.5 h-4 w-4" />{/if}Make it 3D
					</Button>
					{#if st.progress}
						<Button class="rounded-full" onclick={stop} size="sm" type="button" variant="ghost"><Square class="mr-1.5 h-3.5 w-3.5" />Stop</Button>
					{/if}
				</div>
			</form>

			{#if st.progress}
				<div class="mt-3 text-sm text-muted-foreground">
					<p>{stage} {Math.floor(elapsed / 60)}:{String(elapsed % 60).padStart(2, '0')}{st.progress.quality ? ` · ${QNAME[st.progress.quality] ?? ''}${st.progress.family === 'pixal3d' ? ', Pixal3D' : ''}` : ''}</p>
					{#if st.progress.detail}<p class="mt-1 truncate font-mono text-xs">{st.progress.detail}</p>{/if}
				</div>
			{/if}

			{#if made}
				<figure class="mt-4">
					<ModelViewer src={made.url} />
					<figcaption class="mt-1 text-xs text-muted-foreground">
						{made.model}, {QNAME[made.quality] ?? made.quality}{made.fell_back ? ' (Best did not fit the card)' : ''}{made.matte ? ', matte' : ''}, {made.seconds >= 60 ? `${Math.floor(made.seconds / 60)} min ${Math.round(made.seconds % 60)} s` : `${Math.round(made.seconds)} s`}{made.swapped ? ', the text model paused meanwhile' : ''}. Saved in Files as {made.path}{made.picture ? `, from the picture ${made.picture}` : ''}.
						<a class="underline" download href={made.url}>Download</a>
					</figcaption>
				</figure>
			{/if}
		{/if}
	{/if}
</Card>
