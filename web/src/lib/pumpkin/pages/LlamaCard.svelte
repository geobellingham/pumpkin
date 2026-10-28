<script lang="ts">
	import { CircleAlert, CircleCheck, Download, LoaderCircle, RefreshCw, Undo2 } from '@lucide/svelte';
	import { Button } from '$lib/components/ui/button';
	import Card from '../components/Card.svelte';
	import Progress from '../components/Progress.svelte';
	import Segmented from '../components/Segmented.svelte';
	import Select from '../components/Select.svelte';
	import { api, pumpkin } from '../store.svelte';
	import type { LlamaInfo } from '../types';
	import { onDestroy, onMount } from 'svelte';

	let { compact = false, ondone }: { compact?: boolean; ondone?: () => void } = $props();

	type Job = { done: number; error: string | null; id: string; state: string; tag?: string; total: number };

	let info = $state<LlamaInfo | null>(null);
	let backend = $state('vulkan');
	let job = $state<Job | null>(null);
	let checking = $state(false);
	let timer: ReturnType<typeof setInterval> | undefined;

	const up = $derived(info?.update);

	async function load(fresh = false) {
		const r = await pumpkin.run(() => api<LlamaInfo>(`llama${fresh ? '?fresh=1' : ''}`));

		if (!r) return;

		const first = info === null;

		info = r;

		if (first) backend = r.update?.backend || r.backend;

		// a download started elsewhere (the Overview's "Update and try again", the automatic update): follow it
		if (r.update?.job?.state === 'running' && !job) follow(r.update.job);
	}

	onMount(async () => {
		await load();

		// ask GitHub when the last look is a few hours old (or never happened)
		const checked = info?.update?.checked ?? 0;

		if (!compact && info?.platform !== 'android' && Date.now() / 1000 - checked > 3 * 3600) await check();
	});
	onDestroy(() => clearInterval(timer));

	async function check() {
		checking = true;
		await load(true);
		checking = false;
	}

	function follow(j: Job) {
		job = j;
		clearInterval(timer);
		timer = setInterval(async () => {
			job = await api<Job>(`jobs/${job!.id}`).catch(() => job);

			if (job && job.state !== 'running') {
				clearInterval(timer);

				if (job.state === 'done') {
					const was = info?.info?.version;

					await load();

					const now = info?.info?.version;

					pumpkin.say(was && now === was ? `llama.cpp ${now} is already the newest` : `llama.cpp ${now ?? ''} is ready`);
					ondone?.();
				}
			}
		}, 800);
	}

	async function download() {
		const j = await pumpkin.run(() => api<Job>('llama/update', { body: { backend } }));

		if (j) follow(j);
	}

	async function goBack() {
		const r = await pumpkin.run(() => api('llama/back', { body: {} }), 'Went back to the llama.cpp from before');

		if (r) await load();
	}

	async function useDownloaded(path: string) {
		const r = await pumpkin.run(() => api('llama/use', { body: { path } }), "Using Pumpkin's llama.cpp");

		if (r) await load();
	}

	async function setting(changes: Record<string, string>) {
		await pumpkin.run(() => api('settings', { body: changes, method: 'PUT' }));
		await load('llama_channel' in changes);
	}

	// a Mac gets the Metal build (Apple Silicon) or the CPU build (Intel); the rest is for Linux and Windows
	const BACKENDS = $derived(
		info?.platform === 'darwin'
			? [
					{ label: 'Metal: Apple Silicon GPUs', value: 'metal' },
					{ label: 'CPU only', value: 'cpu' }
				]
			: [
					{ label: 'Vulkan: AMD, Intel and most NVIDIA GPUs', value: 'vulkan' },
					{ label: 'CUDA: NVIDIA GPUs (fastest there)', value: 'cuda' },
					{ label: 'CPU only', value: 'cpu' }
				]
	);

	const MODES = [
		{ label: 'Off', value: 'off' },
		{ label: 'Tell me', value: 'notify' },
		{ label: 'By itself', value: 'auto' }
	];

	const CHANNELS = [
		{ label: 'Latest release, a day old', value: 'release' },
		{ label: 'Newest build (for brand-new models)', value: 'newest' }
	];

	const skipped = $derived(Object.entries(up?.skipped ?? {}));
</script>

<Card description={compact ? '' : 'The llama.cpp engine that loads and runs the models.'} title="Model server">
	{#if info === null}
		<div class="flex justify-center py-6"><LoaderCircle class="h-5 w-5 animate-spin text-muted-foreground" /></div>
	{:else}
		{#if info.path && info.info?.ok}
			<div class="flex gap-3 text-sm">
				<CircleCheck class="mt-0.5 h-4 w-4 shrink-0 text-emerald-500" />
				<div class="min-w-0 flex-1">
					<div class="font-medium">llama-server {info.info.version ?? ''} {info.info.backends.length ? `(${info.info.backends.join(', ')})` : ''}</div>
					<div class="truncate text-xs text-muted-foreground" title={info.path}>{info.path}</div>
					{#if !compact && up && info.platform !== 'android'}
						<div class="mt-1 text-xs text-muted-foreground">
							{#if checking}
								Looking for a newer one…
							{:else if up.available}
								<span class="font-medium text-foreground">{up.latest} is out.</span>
							{:else if up.latest}
								Up to date.
							{/if}
						</div>
					{/if}
					{#if info.info.gpu_error}
						<div class="mt-1 text-xs text-amber-600 dark:text-amber-400">
							This build cannot use the GPU here: {info.info.gpu_error}. Models would not start. Go back to the build before, or update the system.
						</div>
					{/if}
					{#if !info.info.flags.includes('--agent')}
						<div class="mt-1 text-xs text-amber-600 dark:text-amber-400">This build has no agent tools. Update it below.</div>
					{/if}
					{#if !compact && up?.own}
						<div class="mt-1 text-xs text-muted-foreground">
							This is your own build, so Pumpkin never replaces it.
							{#if up.own_older && up.own_older_path}
								<span class="text-amber-600 dark:text-amber-400">It is older than {up.own_older}, which Pumpkin downloaded.</span>
								<button class="underline underline-offset-2" onclick={() => useDownloaded(up.own_older_path!)} type="button">Use {up.own_older}</button>
							{/if}
						</div>
					{/if}
				</div>
			</div>
		{:else if info.path}
			<div class="flex gap-3 text-sm text-destructive">
				<CircleAlert class="mt-0.5 h-4 w-4 shrink-0" />
				<div>{info.path} does not run: {info.info?.error}</div>
			</div>
		{:else}
			<p class="text-sm text-muted-foreground">Not installed yet. Pumpkin downloads the newest official build for your GPU (about 30–300 MB).</p>
		{/if}

		{#if info.platform === 'android'}
			<p class="mt-3 text-sm text-muted-foreground">Built into the app. It runs models on the phone's processor.</p>
		{:else}
			<div class="mt-4 flex flex-col gap-2 sm:flex-row">
				<div class="flex-1"><Select ariaLabel="Build" bind:value={backend} options={BACKENDS} /></div>
				<Button class="rounded-full" disabled={job?.state === 'running'} onclick={download}>
					<Download class="h-4 w-4" />
					{info.path ? 'Update llama.cpp' : 'Download'}
				</Button>
			</div>

			{#if job?.state === 'running'}
				<div class="mt-3"><Progress done={job.done} label="Downloading llama.cpp {job.tag ?? ''}" total={job.total} /></div>
			{:else if job?.state === 'error'}
				<p class="mt-3 text-sm text-destructive">{job.error}</p>
			{:else if job?.state === 'done' && pumpkin.state?.server.state === 'ready'}
				<p class="mt-3 text-sm text-muted-foreground">The running model keeps the old one until it starts again.</p>
			{/if}

			{#if !compact && up}
				{#if up.previous?.version}
					<div class="mt-3 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
						<span>{up.previous.version} is kept in case the new one gives trouble.</span>
						<Button class="h-7 rounded-full px-3 text-xs" onclick={goBack} size="sm" variant="outline">
							<Undo2 class="h-3.5 w-3.5" /> Go back to {up.previous.version}
						</Button>
					</div>
				{/if}

				<div class="mt-5 flex flex-col gap-3 border-t border-border/60 pt-4">
					<div class="flex flex-wrap items-center justify-between gap-2">
						<div>
							<div class="text-sm font-medium">Keep llama.cpp up to date</div>
							<div class="text-xs text-muted-foreground">
								{#if up.mode === 'auto'}
									Updates once a day at most, while no model is running. A build that breaks the chat is undone.
								{:else if up.mode === 'notify'}
									The Overview says so when yours is a week behind. New models often need a newer one.
								{:else}
									Only when you press Update.
								{/if}
								{#if up.own}Your own build is left alone either way.{/if}
							</div>
						</div>
						<Segmented onchange={(v) => setting({ llama_updates: v })} options={MODES} value={up.mode} />
					</div>
					<div class="flex flex-col gap-1.5">
						<div class="text-sm font-medium">Which builds</div>
						<div class="text-xs text-muted-foreground">llama.cpp puts out a few builds a day. Waiting a day means a broken one is usually fixed before you get it.</div>
						<Select ariaLabel="Which builds" onchange={(v) => setting({ llama_channel: v })} options={CHANNELS} value={up.channel} />
					</div>
					<div class="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
						<Button class="h-7 rounded-full px-3 text-xs" disabled={checking} onclick={check} size="sm" variant="ghost">
							<RefreshCw class={['h-3.5 w-3.5', checking && 'animate-spin']} /> Check now
						</Button>
						{#if up.pinned}<span>The chat was built with {up.pinned}. Each new build is tested against it the first time it runs.</span>{/if}
					</div>
					{#if skipped.length}
						<details class="text-xs text-muted-foreground">
							<summary class="cursor-pointer">Skipped builds ({skipped.length})</summary>
							<ul class="mt-1 flex flex-col gap-0.5">
								{#each skipped as [tag, why] (tag)}<li><span class="font-medium">{tag}</span>: {why}</li>{/each}
							</ul>
						</details>
					{/if}
				</div>
			{/if}
		{/if}

		{#if !compact && info.log}
			<details class="mt-4 text-sm">
				<summary class="cursor-pointer font-medium">Server log</summary>
				<pre class="mt-2 max-h-72 overflow-auto rounded-xl bg-muted/60 p-3 font-mono text-xs break-all whitespace-pre-wrap">{info.log}</pre>
			</details>
		{/if}
	{/if}
</Card>
