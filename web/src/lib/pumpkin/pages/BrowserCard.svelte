<script lang="ts">
	// The Pumpkin browser extension: pair a browser, see which browsers are paired, and the sites Pumpkin may use.
	import { Download, LoaderCircle, Trash2, X } from '@lucide/svelte';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import Card from '../components/Card.svelte';
	import { api, pumpkin } from '../store.svelte';
	import { onDestroy, onMount } from 'svelte';

	interface Paired {
		created: number;
		id: string;
		last_used: number;
		name: string;
	}

	interface Status {
		connected: boolean;
		paired: Paired[];
		sites: { allowed: string[]; blocked: string[] };
		tabs: number;
	}

	let status = $state<Status | null>(null);
	let code = $state<{ code: string; expires: number } | null>(null);
	let now = $state(Date.now() / 1000);
	let making = $state(false);
	let timer: ReturnType<typeof setInterval> | undefined;

	async function load() {
		status = await api<Status>('browser').catch(() => status);
	}

	onMount(() => {
		void load();
		timer = setInterval(() => {
			now = Date.now() / 1000;
			void load();
		}, 5000);
	});

	onDestroy(() => clearInterval(timer));

	const left = $derived(code ? Math.max(0, Math.round(code.expires - now)) : 0);

	async function makeCode() {
		making = true;
		const r = await pumpkin.run(() => api<{ code: string; expires: number }>('browser/code', { body: {} }));
		making = false;

		if (r) code = r;
	}

	async function remove(p: Paired) {
		await pumpkin.run(() => api(`browser/paired/${p.id}`, { method: 'DELETE', body: {} }), 'Browser removed');
		await load();
	}

	async function saveSites(allowed: string[], blocked: string[]) {
		const r = await pumpkin.run(() => api<Status['sites']>('browser/sites', { method: 'PUT', body: { allowed, blocked } }));

		if (r && status) status = { ...status, sites: r };
	}

	function ago(t: number) {
		const s = Math.max(0, Date.now() / 1000 - t);

		if (s < 60) return 'just now';
		if (s < 3600) return `${Math.round(s / 60)} min ago`;
		if (s < 86400) return `${Math.round(s / 3600)} h ago`;

		return new Date(t * 1000).toLocaleDateString();
	}
</script>

<Card
	description="Let Pumpkin work in your own Chrome or Edge: read pages, click, type. It asks you first, and the model stays on your computer."
	title="Browser extension"
>
	{#if !status}
		<div class="flex justify-center py-6"><LoaderCircle class="h-5 w-5 animate-spin text-muted-foreground" /></div>
	{:else}
		<p class="text-sm">
			{#if status.connected}
				<span class="text-emerald-600 dark:text-emerald-400">Connected</span> — the Pumpkin panel is open in a browser.
			{:else}
				<span class="text-muted-foreground">Not connected right now.</span> Open the Pumpkin panel in the browser (the extension's icon) and leave it open while Pumpkin works.
			{/if}
		</p>

		<h3 class="mt-4 mb-1 text-sm font-medium">Set it up</h3>
		<ol class="list-decimal space-y-1 pl-5 text-sm text-muted-foreground">
			<li>
				Download the extension and unzip it.
				<a class="ml-1 inline-flex items-center gap-1 text-primary underline-offset-2 hover:underline" download href="./pumpkin/api/browser/extension.zip">
					<Download class="h-3.5 w-3.5" /> pumpkin-extension.zip
				</a>
			</li>
			<li>In Chrome or Edge open <span class="font-mono text-xs">chrome://extensions</span>, turn on Developer mode, and choose <b>Load unpacked</b>. Pick the unzipped folder.</li>
			<li>Click the Pumpkin icon in the toolbar. Make a code here and type it in the panel.</li>
		</ol>

		<div class="mt-3 flex flex-wrap items-center gap-3">
			<Button class="rounded-full" disabled={making} onclick={makeCode} size="sm" variant="secondary">Make a pairing code</Button>

			{#if code && left > 0}
				<Input class="h-9 w-40 text-center font-mono text-base tracking-widest" readonly value={code.code} />
				<span class="text-xs text-muted-foreground">Works once, for {Math.ceil(left / 60)} more min.</span>
			{:else if code}
				<span class="text-xs text-muted-foreground">That code has expired. Make a new one.</span>
			{/if}
		</div>

		<h3 class="mt-5 mb-2 text-sm font-medium">Paired browsers</h3>
		{#if status.paired.length}
			<ul class="flex flex-col gap-2">
				{#each status.paired as p (p.id)}
					<li class="flex items-center justify-between gap-3 rounded-xl bg-muted/40 px-3 py-2 text-sm">
						<span class="min-w-0">
							<span class="block truncate">{p.name}</span>
							<span class="text-xs text-muted-foreground">Paired {ago(p.created)}, last seen {ago(p.last_used)}</span>
						</span>
						<Button aria-label="Remove this browser" class="size-8 shrink-0 rounded-full" onclick={() => remove(p)} size="icon" variant="ghost">
							<Trash2 class="h-4 w-4" />
						</Button>
					</li>
				{/each}
			</ul>
		{:else}
			<p class="text-sm text-muted-foreground">None yet.</p>
		{/if}

		<h3 class="mt-5 mb-1 text-sm font-medium">Sites</h3>
		<p class="mb-2 text-xs text-muted-foreground">
			Pumpkin asks the first time it wants a site. Say "Always" to keep it here. Banks, mail, password managers and local addresses are always asked about and never kept.
		</p>
		{#each [{ key: 'allowed', title: 'Always allowed' }, { key: 'blocked', title: 'Never allowed' }] as group (group.key)}
			{@const items = status.sites[group.key as 'allowed' | 'blocked']}
			{#if items.length}
				<p class="mt-2 text-xs font-medium text-muted-foreground">{group.title}</p>
				<ul class="mt-1 flex flex-wrap gap-2">
					{#each items as site (site)}
						<li class="inline-flex items-center gap-1 rounded-full bg-muted/60 py-1 pr-1 pl-3 text-xs">
							<span class="font-mono">{site}</span>
							<button
								aria-label={`Forget ${site}`}
								class="rounded-full p-1 hover:bg-background"
								onclick={() =>
									saveSites(
										status!.sites.allowed.filter((x) => x !== site),
										status!.sites.blocked.filter((x) => x !== site)
									)}
							>
								<X class="h-3 w-3" />
							</button>
						</li>
					{/each}
				</ul>
			{/if}
		{/each}
		{#if !status.sites.allowed.length && !status.sites.blocked.length}
			<p class="text-sm text-muted-foreground">No sites kept yet.</p>
		{/if}
	{/if}
</Card>
