<script lang="ts">
	import { CircleCheck, Cpu, LoaderCircle, MemoryStick } from '@lucide/svelte';
	import { Button } from '$lib/components/ui/button';
	import Card from '../components/Card.svelte';
	import PumpkinLogo from '../components/PumpkinLogo.svelte';
	import { api, pumpkin } from '../store.svelte';
	import type { Hardware } from '../types';
	import LlamaCard from './LlamaCard.svelte';
	import Models from './Models.svelte';
	import { onMount } from 'svelte';

	let hw = $state<Hardware | null>(null);
	let step = $state(1);

	onMount(async () => {
		hw = await pumpkin.run(() => api<Hardware>('hardware?fresh=1'));
	});

	async function finish() {
		await pumpkin.run(() => api('settings', { body: { setup_done: true }, method: 'PUT' }));
		location.hash = pumpkin.state?.has_models ? '#/pumpkin/overview' : '#/pumpkin/models';
	}

	const s = $derived(pumpkin.state);
</script>

<div class="flex flex-col gap-5">
	<div class="flex flex-col items-center gap-2 pt-2 text-center">
		<PumpkinLogo class="size-14" />
		<h2 class="text-2xl font-semibold tracking-tight">Welcome to Pumpkin</h2>
		<p class="max-w-md text-sm text-muted-foreground">
			Run AI models and agents on your own computer. Nothing leaves it unless you turn on phone access.
		</p>
	</div>

	<ol class="flex justify-center gap-2 text-xs text-muted-foreground" aria-label="Setup steps">
		{#each ['Your computer', 'Model server', 'First model'] as label, i (label)}
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
								<div class="text-muted-foreground">{hw.main_gpu.vram_total ? `${hw.main_gpu.vram_total.toFixed(0)} GB of video memory` : 'Shares system memory'}</div>
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
				<div class="mt-5 flex justify-end"><Button class="rounded-full" onclick={() => (step = 2)}>Continue</Button></div>
			{/if}
		</Card>
	{:else if step === 2}
		<LlamaCard compact ondone={() => (step = 3)} />
		<div class="flex justify-between">
			<Button class="rounded-full" onclick={() => (step = 1)} variant="ghost">Back</Button>
			<Button class="rounded-full" disabled={!s?.has_server} onclick={() => (step = 3)}>Continue</Button>
		</div>
	{:else}
		{#if s?.has_models}
			<Card>
				<div class="flex items-center gap-3 text-sm">
					<CircleCheck class="h-5 w-5 shrink-0 text-emerald-500" />
					You already have models. You can install more any time under Models.
				</div>
			</Card>
		{/if}
		<Models />
		<div class="flex justify-between">
			<Button class="rounded-full" onclick={() => (step = 2)} variant="ghost">Back</Button>
			<Button class="rounded-full" onclick={finish}>{s?.has_models || s?.installs.length ? 'Finish' : 'Skip for now'}</Button>
		</div>
	{/if}
</div>
