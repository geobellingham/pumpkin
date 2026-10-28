<script lang="ts">
	import { Copy, LoaderCircle, RefreshCw, Trash2 } from '@lucide/svelte';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Switch } from '$lib/components/ui/switch';
	import qrcode from 'qrcode-generator';
	import BrowserCard from './BrowserCard.svelte';
	import Card from '../components/Card.svelte';
	import Field from '../components/Field.svelte';
	import Toggle from '../components/Toggle.svelte';
	import { api, pumpkin } from '../store.svelte';
	import { onDestroy, onMount } from 'svelte';

	interface DeviceInfo {
		busy?: number;
		model?: string | null;
		models?: string[];
		name?: string;
		platform?: string;
		state?: string;
	}

	interface Device {
		enabled: boolean;
		id: string;
		info?: DeviceInfo;
		name: string;
		online?: boolean;
		problem?: string;
	}

	interface Request {
		at: number;
		error?: string;
		from: string;
		model?: string;
		seconds?: number;
		state: string;
		task: string;
	}

	interface Share {
		addresses: string[];
		android: boolean;
		code: string | null;
		custom_name: string;
		name: string;
		recent: Request[];
		remote: string;
		share: boolean;
	}

	let list = $state<Device[] | null>(null);
	let hasTools = $state<boolean | null>(null);
	let presetTools = $state<boolean | null>(null);
	let presetId = $state<string | null>(null);
	let share = $state<Share | null>(null);
	let code = $state('');
	let name = $state('');
	let nameTouched = false; // typed in but not saved yet
	let adding = $state(false);
	let testing = $state<Record<string, boolean>>({});
	let answers = $state<Record<string, string>>({});

	async function load() {
		const r = await pumpkin.run(() =>
			api<{ devices: Device[]; model_has_tools: boolean | null; preset_id: string | null; preset_tools: boolean | null }>('devices')
		);

		if (r) {
			list = r.devices;
			hasTools = r.model_has_tools;
			presetTools = r.preset_tools;
			presetId = r.preset_id;
		}
	}

	async function loadShare() {
		const r = await pumpkin.run(() => api<Share>('devices/share'));

		if (r) {
			share = r;
			// the 15-second refresh must not undo what you are typing
			if (!nameTouched) name = r.custom_name;
		}
	}

	let timer: ReturnType<typeof setInterval> | undefined;

	onMount(() => {
		void load();
		void loadShare();
		timer = setInterval(() => {
			void load();
			void loadShare();
		}, 15000);
	});

	onDestroy(() => clearInterval(timer));

	const android = $derived(pumpkin.state?.platform === 'android');

	async function add() {
		if (!code.trim()) return;

		adding = true;

		// a running model with tools gets the device tools by itself: Pumpkin restarts it once it is idle
		const r = await pumpkin.run(
			() => api<Device>('devices', { body: { code: code.trim() } }),
			presetTools && hasTools !== true ? 'Device added. The model restarts by itself once it is idle, so it can ask it.' : 'Device added'
		);

		adding = false;

		if (r) {
			code = '';
			await load();
		}
	}

	async function setEnabled(d: Device, on: boolean) {
		await pumpkin.run(() => api(`devices/${d.id}`, { body: { enabled: on }, method: 'PATCH' }));
		await load();
	}

	async function remove(d: Device) {
		if (!confirm(`Remove ${d.name}? You can pair it again with its code.`)) return;

		await pumpkin.run(() => api(`devices/${d.id}`, { method: 'DELETE' }), 'Removed');
		await load();
	}

	async function test(d: Device) {
		testing[d.id] = true;
		answers[d.id] = '';

		const r = await pumpkin.run(() => api<{ answer: string; model: string; seconds: number }>(`devices/${d.id}/test`, { body: {} }));

		testing[d.id] = false;

		if (r) answers[d.id] = `${r.model}, ${r.seconds} s: ${r.answer.replace(/^#+\s*/gm, '').replace(/\s+/g, ' ').trim()}`;

		await load();
	}

	async function restartModel() {
		if (!presetId) return;

		const id = presetId;

		if (presetTools === false) await pumpkin.run(() => api(`presets/${id}`, { body: { tools: true }, method: 'PUT' }));
		await pumpkin.run(() => api('llama/start', { body: { preset: id } }), 'Restarting the model');
		setTimeout(load, 4000);
	}

	async function setShare(on: boolean) {
		const r = await pumpkin.run(() => api<Share>('devices/share', { body: { on } }));

		if (r) share = r;
	}

	async function saveName() {
		const r = await pumpkin.run(() => api<Share>('devices/share', { body: { name } }), 'Saved');

		if (r) {
			share = r;
			nameTouched = false;
		}
	}

	async function newKey() {
		if (!confirm('Make a new code? Devices paired with the old one stop working until you pair them again.')) return;

		const r = await pumpkin.run(() => api<Share>('devices/share', { body: { new_key: true } }), 'New code made');

		if (r) share = r;
	}

	async function copy() {
		if (!share?.code) return;

		try {
			await navigator.clipboard.writeText(share.code);
			pumpkin.say('Code copied');
		} catch {
			pumpkin.say('Copy failed. Select the code and copy it.', 'error');
		}
	}

	const qr = $derived.by(() => {
		if (!share?.code) return '';

		const q = qrcode(0, 'L');

		q.addData(share.code);
		q.make();

		return q.createSvgTag({ cellSize: 4, margin: 2, scalable: true });
	});

	function ago(t: number) {
		const s = Math.max(0, Date.now() / 1000 - t);

		if (s < 60) return 'just now';
		if (s < 3600) return `${Math.round(s / 60)} min ago`;
		if (s < 86400) return `${Math.round(s / 3600)} h ago`;

		return new Date(t * 1000).toLocaleDateString();
	}

	const PLATFORM: Record<string, string> = { android: 'Phone', darwin: 'Mac', linux: 'Linux', win32: 'Windows' };
</script>

<div class="flex flex-col gap-4">
	{#if !android}
		<Card
			description="Your chat model can hand tasks to Pumpkin on your other computers and phones, then use their answers. It gets two tools: list your devices and ask one of them."
			title="Use your other devices"
		>
			{#if list === null}
				<div class="flex justify-center py-6"><LoaderCircle class="h-5 w-5 animate-spin text-muted-foreground" /></div>
			{:else}
				{#if list.length}
					<ul class="flex flex-col divide-y divide-border/60 rounded-xl border border-border/60">
						{#each list as d (d.id)}
							<li class="flex flex-col gap-2 p-3">
								<div class="flex items-center gap-3">
									<span class={['size-2 shrink-0 rounded-full', d.online ? 'bg-emerald-500' : 'bg-muted-foreground/40']}></span>
									<div class="min-w-0 flex-1">
										<div class="truncate text-sm font-medium">
											{d.name}
											{#if d.info?.platform}<span class="font-normal text-muted-foreground"> ({PLATFORM[d.info.platform] ?? d.info.platform})</span>{/if}
										</div>
										<div class="truncate text-xs text-muted-foreground">
											{#if d.online}
												{d.info?.model ? `Running ${d.info.model}` : `Starts ${d.info?.models?.[0] ?? 'its model'} when asked`}
											{:else}
												{d.problem ?? 'Not reachable'}
											{/if}
										</div>
									</div>
									<Switch aria-label="Let the model ask {d.name}" checked={d.enabled} onCheckedChange={(v) => setEnabled(d, v)} />
								</div>
								<div class="flex flex-wrap items-center gap-2 pl-5">
									<Button class="h-8 rounded-full" disabled={!d.online || !d.enabled || testing[d.id]} onclick={() => test(d)} size="sm" variant="secondary">
										{#if testing[d.id]}<LoaderCircle class="h-3.5 w-3.5 animate-spin" />{/if}
										Test
									</Button>
									<Button aria-label="Remove {d.name}" class="size-8 rounded-full" onclick={() => remove(d)} size="icon" variant="ghost">
										<Trash2 class="h-4 w-4" />
									</Button>
									{#if answers[d.id]}<span class="min-w-0 flex-1 text-xs break-words text-muted-foreground">{answers[d.id]}</span>{/if}
								</div>
							</li>
						{/each}
					</ul>

					{#if hasTools === false}
						<div class="mt-3 flex flex-wrap items-center gap-3 rounded-xl bg-muted/50 p-3 text-sm">
							<span class="min-w-0 flex-1">
								{presetTools === false
									? 'Tools are off in the running preset, so the model cannot ask your devices.'
									: 'The running model started before you paired, so it does not have the device tools yet. Pumpkin restarts it by itself once nobody is using it, or you can do it now.'}
							</span>
							<Button class="rounded-full" onclick={restartModel} size="sm">
								<RefreshCw class="h-4 w-4" />
								{presetTools === false ? 'Turn on tools and restart' : 'Restart the model'}
							</Button>
						</div>
					{/if}
				{/if}

				<div class={list.length ? 'mt-4' : ''}>
					<Field hint="On the other device open Pumpkin > Devices, turn on sharing and copy its code." label="Add a device">
						<div class="flex gap-2">
							<Input bind:value={code} onkeydown={(e) => e.key === 'Enter' && add()} placeholder="Paste the pairing code" />
							<Button class="shrink-0 rounded-full" disabled={adding || !code.trim()} onclick={add}>
								{#if adding}<LoaderCircle class="h-4 w-4 animate-spin" />{/if}
								Add
							</Button>
						</div>
					</Field>
				</div>

				<p class="mt-3 text-xs text-muted-foreground">
					Works best with a model that is good at using tools. Each request shows up in the chat like a web search. The other device only sees the task text, never this chat.
				</p>
			{/if}
		</Card>
	{/if}

	<Card
		description={android
			? "Your computer's Pumpkin can send tasks to the model on this phone."
			: 'Your other Pumpkins can send tasks to the model on this device.'}
		title="Share this device"
	>
		{#if !share}
			<div class="flex justify-center py-6"><LoaderCircle class="h-5 w-5 animate-spin text-muted-foreground" /></div>
		{:else}
			<Toggle
				checked={share.share}
				description="They get answers only. No access to your files, settings or chats."
				label="Let my other devices use this one"
				onchange={setShare}
			/>

			{#if share.share}
				{#if share.code}
					<div class="mt-4 flex flex-col items-center gap-4 rounded-xl bg-muted/40 p-4 sm:flex-row sm:items-start">
						<div class="w-36 shrink-0 overflow-hidden rounded-lg bg-white p-1">
							{@html qr}
						</div>
						<div class="w-full min-w-0 flex-1 text-sm">
							<p class="font-medium">Pairing code</p>
							<p class="mt-1 text-muted-foreground">Paste it into Devices on the other Pumpkin. Keep it private: it lets a device use this model.</p>
							<div class="mt-3 flex gap-2">
								<Input class="h-9 font-mono text-xs" readonly value={share.code} />
								<Button aria-label="Copy code" class="size-9 shrink-0 rounded-full" onclick={copy} size="icon" variant="secondary"><Copy class="h-4 w-4" /></Button>
							</div>
							<p class="mt-2 text-xs break-all text-muted-foreground">Reachable at {share.addresses.join(', ')}</p>
						</div>
					</div>
				{:else}
					<p class="mt-3 text-sm text-destructive">This device has no network address right now. Connect to Wi-Fi or Tailscale.</p>
				{/if}

				{#if share.remote === 'lan'}
					<p class="mt-3 text-xs text-muted-foreground">
						Pumpkin now also listens on your home network and Tailscale. Everything except asking still needs your sign-in key.
					</p>
				{/if}

				<div class="mt-4 flex flex-col gap-3">
					<Field hint="How your other devices see this one." label="Name">
						<div class="flex gap-2">
							<Input bind:value={name} oninput={() => (nameTouched = true)} placeholder={share.name} />
							<Button class="shrink-0 rounded-full" onclick={saveName} size="sm" variant="secondary">Save</Button>
						</div>
					</Field>
					<div>
						<Button class="rounded-full" onclick={newKey} size="sm" variant="ghost">Make a new code</Button>
					</div>
				</div>

				{#if share.recent.length}
					<h3 class="mt-4 mb-2 text-sm font-medium">Recent requests</h3>
					<ul class="flex flex-col gap-2">
						{#each share.recent as r (r.at)}
							<li class="rounded-xl bg-muted/40 px-3 py-2 text-sm">
								<div class="flex flex-wrap items-baseline justify-between gap-x-2 text-xs text-muted-foreground">
									<span>From {r.from || 'another device'}, {ago(r.at)}</span>
									<span class={r.state === 'error' ? 'text-destructive' : ''}>
										{r.state === 'running' ? 'Working…' : r.state === 'error' ? 'Failed' : `${r.seconds} s`}
									</span>
								</div>
								<p class="mt-1 line-clamp-2 break-words">{r.task}</p>
								{#if r.error}<p class="mt-1 text-xs break-words text-destructive">{r.error}</p>{/if}
							</li>
						{/each}
					</ul>
				{/if}
			{/if}
		{/if}
	</Card>

	{#if !android}
		<BrowserCard />
	{/if}

	{#if android}
		<p class="px-1 text-xs text-muted-foreground">
			A phone can share its model, but cannot hand out tasks itself yet.
		</p>
	{/if}
</div>
