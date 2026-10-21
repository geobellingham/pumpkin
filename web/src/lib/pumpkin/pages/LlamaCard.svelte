<script lang="ts">
	import { CircleAlert, CircleCheck, Download, LoaderCircle } from '@lucide/svelte';
	import { Button } from '$lib/components/ui/button';
	import Card from '../components/Card.svelte';
	import Progress from '../components/Progress.svelte';
	import Select from '../components/Select.svelte';
	import { api, pumpkin } from '../store.svelte';
	import type { LlamaInfo } from '../types';
	import { onDestroy, onMount } from 'svelte';

	let { compact = false, ondone }: { compact?: boolean; ondone?: () => void } = $props();

	let info = $state<LlamaInfo | null>(null);
	let backend = $state('vulkan');
	let job = $state<{ done: number; error: string | null; id: string; state: string; total: number } | null>(null);
	let timer: ReturnType<typeof setInterval> | undefined;

	async function load() {
		info = await pumpkin.run(() => api<LlamaInfo>('llama'));

		if (info) backend = info.backend;
	}

	onMount(load);
	onDestroy(() => clearInterval(timer));

	async function download() {
		job = await pumpkin.run(() => api<typeof job>('llama/download', { body: { backend } }));

		if (!job) return;

		timer = setInterval(async () => {
			job = await api<typeof job>(`jobs/${job!.id}`).catch(() => job);

			if (job && job.state !== 'running') {
				clearInterval(timer);

				if (job.state === 'done') {
					pumpkin.say('llama-server is ready');
					await load();
					ondone?.();
				}
			}
		}, 800);
	}

	// a Mac gets the Metal build (Apple Silicon) or the CPU build (Intel); the rest is for Linux
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
</script>

<Card description={compact ? '' : 'The llama.cpp engine that loads and runs the models.'} title="Model server">
	{#if info === null}
		<div class="flex justify-center py-6"><LoaderCircle class="h-5 w-5 animate-spin text-muted-foreground" /></div>
	{:else}
		{#if info.path && info.info?.ok}
			<div class="flex gap-3 text-sm">
				<CircleCheck class="mt-0.5 h-4 w-4 shrink-0 text-emerald-500" />
				<div class="min-w-0">
					<div class="font-medium">llama-server {info.info.version ?? ''} {info.info.backends.length ? `(${info.info.backends.join(', ')})` : ''}</div>
					<div class="truncate text-xs text-muted-foreground" title={info.path}>{info.path}</div>
					{#if !info.info.flags.includes('--agent')}
						<div class="mt-1 text-xs text-amber-600 dark:text-amber-400">This build has no agent tools. Download a newer one below.</div>
					{/if}
				</div>
			</div>
		{:else if info.path}
			<div class="flex gap-3 text-sm text-destructive">
				<CircleAlert class="mt-0.5 h-4 w-4 shrink-0" />
				<div>{info.path} does not run: {info.info?.error}</div>
			</div>
		{:else}
			<p class="text-sm text-muted-foreground">Not installed yet. Pumpkin can download the official build for your GPU (about 30–300 MB).</p>
		{/if}

		{#if info.platform === 'android'}
			<p class="mt-3 text-sm text-muted-foreground">Built into the app. It runs models on the phone's processor.</p>
		{:else}
		<div class="mt-4 flex flex-col gap-2 sm:flex-row">
			<div class="flex-1"><Select ariaLabel="Build" bind:value={backend} options={BACKENDS} /></div>
			<Button class="rounded-full" disabled={job?.state === 'running'} onclick={download}>
				<Download class="h-4 w-4" />
				{info.path ? 'Download latest' : 'Download'}
			</Button>
		</div>

		{#if job?.state === 'running'}
			<div class="mt-3"><Progress done={job.done} label="Downloading llama.cpp" total={job.total} /></div>
		{:else if job?.state === 'error'}
			<p class="mt-3 text-sm text-destructive">{job.error}</p>
		{/if}
		{/if}

		{#if !compact && info.log}
			<details class="mt-4 text-sm">
				<summary class="cursor-pointer font-medium">Server log</summary>
				<pre class="mt-2 max-h-72 overflow-auto rounded-xl bg-muted/60 p-3 font-mono text-xs whitespace-pre-wrap break-all">{info.log}</pre>
			</details>
		{/if}
	{/if}
</Card>
