<script lang="ts">
	import { Check, Copy, LoaderCircle, RefreshCw } from '@lucide/svelte';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import qrcode from 'qrcode-generator';
	import Card from '../components/Card.svelte';
	import Field from '../components/Field.svelte';
	import { api, pumpkin } from '../store.svelte';
	import type { Settings } from '../types';
	import { onDestroy, onMount } from 'svelte';

	interface Tailscale {
		host: string | null;
		https: boolean;
		installed: boolean;
		magic_dns: boolean;
		running: boolean;
		signed_in: boolean;
	}

	interface Remote {
		base: string | null;
		error: string | null;
		lan_ip: string | null;
		link: string | null;
		mode: string;
		tailscale: Tailscale | null;
		tailscale_installed: boolean;
	}

	let remote = $state<Remote | null>(null);
	let settings = $state<Settings | null>(null);
	let busy = $state(false);
	let once = $state<{ expires: number; link: string } | null>(null);
	let showKey = $state(false);
	let timer: ReturnType<typeof setInterval> | undefined;

	async function newCode() {
		once = null;

		if (!remote?.base) return;

		once = await api<{ expires: number; link: string }>('remote/qr', { body: {} }).catch((e) => {
			pumpkin.say(e.message, 'error');

			return null;
		});
	}

	onMount(async () => {
		remote = await pumpkin.run(() => api<Remote>('remote'));
		settings = await pumpkin.run(() => api<Settings>('settings'));
		void newCode();
		// a code works once and for ten minutes: keep a fresh one on screen
		timer = setInterval(() => {
			if (remote?.base && (!once || once.expires - Date.now() / 1000 < 60)) void newCode();
		}, 20_000);
	});

	onDestroy(() => clearInterval(timer));

	async function setMode(mode: string) {
		busy = true;

		const r = await pumpkin.run(() => api<Remote>('remote', { body: { mode } }));

		busy = false;

		if (r) {
			remote = r;
			showKey = false;
			void newCode();
		}
	}

	async function recheck() {
		remote = (await pumpkin.run(() => api<Remote>('remote'))) ?? remote;
		void newCode();
	}

	const qr = $derived.by(() => {
		if (!once?.link) return '';

		const q = qrcode(0, 'M');

		q.addData(once.link);
		q.make();

		return q.createSvgTag({ cellSize: 5, margin: 2, scalable: true });
	});

	async function copy(text: string | null | undefined) {
		if (!text) return;

		try {
			await navigator.clipboard.writeText(text);
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

	const platform = $derived(pumpkin.state?.platform ?? '');
	const installHow = $derived(
		platform === 'linux' ? 'Run: curl -fsSL https://tailscale.com/install.sh | sh' : 'Download it from tailscale.com/download and open it.'
	);

	const steps = $derived.by(() => {
		const t = remote?.tailscale;

		if (!t) return [];

		return [
			{ done: t.installed, how: installHow, label: 'Install Tailscale on this computer' },
			{ done: t.signed_in, how: platform === 'linux' ? 'Run: sudo tailscale up' : 'Open Tailscale and sign in (Google, Microsoft or Apple account).', label: 'Sign in to Tailscale' },
			{ done: t.magic_dns && t.https, how: 'In the Tailscale admin page, open DNS and turn on MagicDNS and HTTPS Certificates.', label: 'Turn on MagicDNS and HTTPS', url: 'https://login.tailscale.com/admin/dns' },
			{ done: false, how: 'Get Tailscale from the app store and sign in with the same account.', label: 'Install Tailscale on your phone' }
		];
	});

	const MODES = [
		{ desc: 'Only this computer can open Pumpkin.', label: 'Off', value: 'off' },
		{ desc: 'Any device on your Wi-Fi can open it after scanning. Nothing to install. Not encrypted, so only on a network you trust.', label: 'Home network', value: 'lan' },
		{ desc: 'Works anywhere, encrypted. Needs the free Tailscale app on this computer and your phone.', label: 'Anywhere (Tailscale)', value: 'tailscale' }
	];
</script>

<div class="flex flex-col gap-4">
	<Card description="Use Pumpkin from your phone with the models on this computer: the same chat, documents, tasks and voice." title="Phone access">
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

			{#if remote.error}
				<p class="mt-3 text-sm text-destructive">{remote.error}</p>
			{/if}

			{#if remote.mode === 'tailscale' && steps.length && !remote.base}
				<ol class="mt-4 flex flex-col gap-2">
					{#each steps as st, i (st.label)}
						<li class="flex items-start gap-3 rounded-xl bg-muted/40 px-3 py-2">
							<span class={['mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full text-xs', st.done ? 'bg-foreground text-background' : 'border border-muted-foreground/50']}>
								{#if st.done}<Check class="h-3 w-3" />{:else}{i + 1}{/if}
							</span>
							<span class="min-w-0 text-sm">
								<span class={['block font-medium', st.done && 'text-muted-foreground line-through']}>{st.label}</span>
								{#if !st.done}
									<span class="block text-xs text-muted-foreground">{st.how}</span>
									{#if st.url}<a class="text-xs underline" href={st.url} rel="noreferrer" target="_blank">Open the DNS settings</a>{/if}
								{/if}
							</span>
						</li>
					{/each}
				</ol>
				<Button class="mt-3 rounded-full" onclick={recheck} size="sm" variant="secondary"><RefreshCw class="h-4 w-4" /> Check again</Button>
			{/if}

			{#if remote.base}
				<div class="mt-4 flex flex-col items-center gap-3 rounded-xl bg-muted/40 p-4 sm:flex-row sm:items-start">
					<div class="flex w-44 shrink-0 items-center justify-center overflow-hidden rounded-lg bg-white p-1">
						{#if qr}{@html qr}{:else}<LoaderCircle class="my-16 h-5 w-5 animate-spin text-neutral-400" />{/if}
					</div>
					<div class="min-w-0 flex-1 text-sm">
						<p class="font-medium">Scan with your phone's camera</p>
						<p class="mt-1 text-muted-foreground">
							It signs your phone in. The code works once, for ten minutes, and a new one appears by itself. Then use Share > Add to Home Screen for an app icon.
						</p>
						{#if remote.mode === 'tailscale'}
							<p class="mt-1 text-xs text-muted-foreground">Tailscale must be on and signed in on the phone.</p>
						{/if}
						<div class="mt-3 flex flex-wrap gap-2">
							<Button class="rounded-full" onclick={newCode} size="sm" variant="secondary"><RefreshCw class="h-4 w-4" /> New code</Button>
							<Button class="rounded-full" onclick={() => (showKey = !showKey)} size="sm" variant="ghost">{showKey ? 'Hide' : 'Show'} the permanent link</Button>
						</div>
						{#if showKey && remote.link}
							<p class="mt-3 text-xs text-muted-foreground">This link has your key in it and always works. Only use it on your own devices.</p>
							<div class="mt-1 flex gap-2">
								<Input class="h-9 font-mono text-xs" readonly value={remote.link} />
								<Button aria-label="Copy link" class="size-9 shrink-0 rounded-full" onclick={() => copy(remote?.link)} size="icon" variant="secondary"><Copy class="h-4 w-4" /></Button>
							</div>
						{/if}
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
