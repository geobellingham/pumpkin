<script lang="ts">
	import { Copy, LoaderCircle } from '@lucide/svelte';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import qrcode from 'qrcode-generator';
	import Card from '../components/Card.svelte';
	import Field from '../components/Field.svelte';
	import { api, pumpkin } from '../store.svelte';
	import type { Settings } from '../types';
	import { onMount } from 'svelte';

	interface Remote {
		lan_ip: string | null;
		link: string | null;
		mode: string;
		tailscale_installed: boolean;
	}

	let remote = $state<Remote | null>(null);
	let settings = $state<Settings | null>(null);
	let busy = $state(false);

	onMount(async () => {
		remote = await pumpkin.run(() => api<Remote>('remote'));
		settings = await pumpkin.run(() => api<Settings>('settings'));
	});

	async function setMode(mode: string) {
		busy = true;

		const r = await pumpkin.run(() => api<Remote>('remote', { body: { mode } }));

		busy = false;

		if (r) remote = r;
	}

	const qr = $derived.by(() => {
		if (!remote?.link) return '';

		const q = qrcode(0, 'M');

		q.addData(remote.link);
		q.make();

		return q.createSvgTag({ cellSize: 5, margin: 2, scalable: true });
	});

	async function copy() {
		if (!remote?.link) return;

		try {
			await navigator.clipboard.writeText(remote.link);
			pumpkin.say('Link copied');
		} catch {
			pumpkin.say('Copy failed. Select the link and copy it.', 'error');
		}
	}

	async function saveNtfy() {
		if (!settings) return;

		const r = await pumpkin.run(() => api<Settings>('settings', { body: { ntfy_server: settings!.ntfy_server, ntfy_topic: settings!.ntfy_topic }, method: 'PUT' }), 'Saved');

		if (r) settings = r;
	}

	function randomTopic() {
		if (!settings) return;

		settings.ntfy_topic = 'pumpkin-' + Array.from(crypto.getRandomValues(new Uint8Array(8)), (b) => b.toString(16).padStart(2, '0')).join('');
	}

	const MODES = [
		{ desc: 'Only this computer can open Pumpkin.', label: 'Off', value: 'off' },
		{ desc: 'Works anywhere, encrypted. Install Tailscale on this computer and your phone and sign in to both.', label: 'Tailscale', value: 'tailscale' },
		{ desc: 'Any device on your Wi-Fi can open it with the key. Not encrypted, so only use it on a network you trust.', label: 'Home network', value: 'lan' }
	];
</script>

<div class="flex flex-col gap-4">
	<Card description="Use Pumpkin from your phone: the same chat, models, tasks and voice." title="Phone access">
		{#if !remote}
			<div class="flex justify-center py-6"><LoaderCircle class="h-5 w-5 animate-spin text-muted-foreground" /></div>
		{:else}
			<div class="flex flex-col gap-2" role="radiogroup">
				{#each MODES as m (m.value)}
					<button
						aria-checked={remote.mode === m.value}
						class={['flex items-start gap-3 rounded-xl border px-3 py-3 text-left', remote.mode === m.value ? 'border-foreground/40 bg-muted/40' : 'border-border/60 hover:bg-muted/30']}
						disabled={busy}
						onclick={() => setMode(m.value)}
						role="radio"
						type="button"
					>
						<span class={['mt-1 size-3.5 shrink-0 rounded-full border-2', remote.mode === m.value ? 'border-foreground bg-foreground' : 'border-muted-foreground/50']}></span>
						<span>
							<span class="block text-sm font-medium">{m.label}</span>
							<span class="block text-xs text-muted-foreground">{m.desc}</span>
						</span>
					</button>
				{/each}
			</div>

			{#if remote.mode === 'tailscale' && !remote.tailscale_installed}
				<p class="mt-3 text-sm text-destructive">Tailscale is not installed on this computer. Run: <code>curl -fsSL https://tailscale.com/install.sh | sh</code></p>
			{/if}

			{#if remote.link}
				<div class="mt-4 flex flex-col items-center gap-3 rounded-xl bg-muted/40 p-4 sm:flex-row sm:items-start">
					<div class="w-44 shrink-0 overflow-hidden rounded-lg bg-white p-1">
						{@html qr}
					</div>
					<div class="min-w-0 flex-1 text-sm">
						<p class="font-medium">Scan with your phone's camera</p>
						<p class="mt-1 text-muted-foreground">
							It signs you in. Then use Share > Add to Home Screen to get an app icon. Keep this link private.
						</p>
						<div class="mt-3 flex gap-2">
							<Input class="h-9 font-mono text-xs" readonly value={remote.link} />
							<Button aria-label="Copy link" class="size-9 shrink-0 rounded-full" onclick={copy} size="icon" variant="secondary"><Copy class="h-4 w-4" /></Button>
						</div>
					</div>
				</div>
			{/if}
		{/if}
	</Card>

	{#if settings}
		<Card description="Get a push notification on your phone when a task finishes, through the free ntfy app. No account needed." title="Phone notifications">
			<div class="flex flex-col gap-4">
				<Field hint="Anyone who knows the topic can read the notifications, so keep it long and random." label="ntfy topic">
					<div class="flex gap-2">
						<Input bind:value={settings.ntfy_topic} placeholder="Leave empty to turn off" />
						<Button class="shrink-0 rounded-full" onclick={randomTopic} size="sm" variant="ghost">Make one</Button>
					</div>
				</Field>
				<Field hint="Change this only if you run your own ntfy server." label="ntfy server">
					<Input bind:value={settings.ntfy_server} />
				</Field>
				<div class="flex flex-wrap items-center gap-2">
					<Button class="rounded-full" onclick={saveNtfy} size="sm">Save</Button>
					{#if settings.ntfy_topic}
						<span class="text-xs text-muted-foreground">In the ntfy app, subscribe to <code>{settings.ntfy_topic}</code>.</span>
					{/if}
				</div>
			</div>
		</Card>
	{/if}
</div>
