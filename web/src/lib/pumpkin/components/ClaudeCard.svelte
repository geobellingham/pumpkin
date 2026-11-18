<script lang="ts">
	import { Copy, LoaderCircle } from '@lucide/svelte';
	import { Button } from '$lib/components/ui/button';
	import Card from './Card.svelte';
	import { api, pumpkin } from '../store.svelte';
	import { onDestroy, onMount } from 'svelte';

	interface Job {
		done?: number;
		error?: string;
		job: string;
		kind: string;
		model?: string | null;
		seconds?: number;
		state: string;
		title: string;
		tokens_per_second?: number;
		total?: number;
		waiting_behind?: number;
		who: string;
	}

	interface Info {
		claude_code: string;
		desktop_config: string;
		jobs: Job[];
		key?: string;
		remote_claude_code: string | null;
		remote_url: string | null;
		speed: number | null;
	}

	let info = $state<Info | null>(null);
	let tab = $state<'code' | 'desktop' | 'remote'>('code');
	let timer: ReturnType<typeof setInterval> | undefined;

	async function load() {
		try {
			info = await api<Info>('claude');
		} catch {
			// the page shows the error from its first load; polling stays quiet
		}
	}

	onMount(() => {
		void pumpkin.run(load);
		timer = setInterval(load, 3000);
	});

	onDestroy(() => clearInterval(timer));

	async function copy(text: string | null | undefined, what: string) {
		if (!text) return;

		try {
			await navigator.clipboard.writeText(text);
			pumpkin.say(`${what} copied`);
		} catch {
			pumpkin.say('Copy failed. Select it and copy it.', 'error');
		}
	}

	async function newKey() {
		if (!confirm('Make a new Claude key? Claude on other computers stops working until you paste the new command there.')) return;

		const r = await pumpkin.run(() => api<Info>('claude/key', { body: {} }), 'New key made');

		if (r) info = r;
	}

	async function cancel(job: Job) {
		if (await pumpkin.run(() => api('claude/cancel', { body: { job: job.job } }), 'Stopped')) void load();
	}

	const shown = $derived(tab === 'code' ? info?.claude_code : tab === 'desktop' ? info?.desktop_config : info?.remote_claude_code);

	function label(j: Job) {
		if (j.state === 'queued') return j.waiting_behind ? `Waiting, ${j.waiting_behind} ahead` : 'Waiting';
		if (j.state === 'running') return j.kind === 'batch' ? `Working, ${j.done ?? 0} of ${j.total}` : 'Working…';
		if (j.state === 'error') return 'Failed';
		if (j.state === 'cancelled') return 'Stopped';

		return `${j.seconds} s${j.tokens_per_second ? ` · ${j.tokens_per_second} tok/s` : ''}`;
	}
</script>

<Card
	description="Claude can hand jobs to your local model: summaries, pulling facts out of files, sorting lists, translations, drafts. It keeps working meanwhile, collects the answers, and saves its own effort and your usage. Your files are read here and never leave this computer."
	title="Use with Claude"
>
	{#if !info}
		<div class="flex justify-center py-6"><LoaderCircle class="h-5 w-5 animate-spin text-muted-foreground" /></div>
	{:else}
		<div class="flex flex-wrap gap-1.5">
			<Button class="rounded-full" onclick={() => (tab = 'code')} size="sm" variant={tab === 'code' ? 'secondary' : 'ghost'}>Claude Code</Button>
			<Button class="rounded-full" onclick={() => (tab = 'desktop')} size="sm" variant={tab === 'desktop' ? 'secondary' : 'ghost'}>Claude Desktop</Button>
			<Button class="rounded-full" onclick={() => (tab = 'remote')} size="sm" variant={tab === 'remote' ? 'secondary' : 'ghost'}>Another computer</Button>
		</div>

		<p class="mt-3 text-sm text-muted-foreground">
			{#if tab === 'code'}
				Run this once in a terminal on this computer. Every Claude Code session can then use Pumpkin.
			{:else if tab === 'desktop'}
				In Claude Desktop open Settings, Developer, Edit Config, and add this to claude_desktop_config.json. Restart Claude Desktop afterwards.
			{:else if info.remote_url}
				For Claude Code on another computer, over Tailscale or your home network. The key in it opens only this connector.
			{:else}
				Turn on phone access on the Phone page first (Tailscale or home network). Then Claude on another computer can reach Pumpkin too.
			{/if}
		</p>

		{#if shown}
			<div class="relative mt-2">
				<pre class="max-h-80 overflow-auto rounded-xl bg-muted/50 p-3 pr-12 font-mono text-xs break-all whitespace-pre-wrap">{shown}</pre>
				<Button aria-label="Copy" class="absolute top-2 right-2 size-8 rounded-full" onclick={() => copy(shown, tab === 'desktop' ? 'Config' : 'Command')} size="icon" variant="secondary"><Copy class="h-3.5 w-3.5" /></Button>
			</div>
		{/if}

		{#if tab === 'remote' && info.remote_url}
			<div class="mt-2">
				<Button class="rounded-full" onclick={newKey} size="sm" variant="ghost">Make a new key</Button>
			</div>
		{/if}

		{#if info.jobs.length}
			<h3 class="mt-4 mb-2 text-sm font-medium">Jobs from Claude</h3>
			<ul class="flex flex-col gap-2">
				{#each info.jobs.slice(0, 12) as j (j.job)}
					<li class="rounded-xl bg-muted/40 px-3 py-2 text-sm">
						<div class="flex flex-wrap items-baseline justify-between gap-x-2 text-xs text-muted-foreground">
							<span>{j.kind === 'batch' ? `Batch of ${j.total}` : 'Job'}{j.model ? ` · ${j.model}` : ''}</span>
							<span class="flex items-center gap-2">
								<span class={j.state === 'error' ? 'text-destructive' : ''}>{label(j)}</span>
								{#if j.state === 'queued' || j.state === 'running'}
									<button class="underline underline-offset-2 hover:text-foreground" onclick={() => cancel(j)} type="button">Stop</button>
								{/if}
							</span>
						</div>
						<p class="mt-1 line-clamp-2 break-words">{j.title}</p>
						{#if j.error}<p class="mt-1 text-xs break-words text-destructive">{j.error}</p>{/if}
					</li>
				{/each}
			</ul>
		{/if}
	{/if}
</Card>
