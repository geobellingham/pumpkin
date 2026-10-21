<script lang="ts">
	import { LoaderCircle } from '@lucide/svelte';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Textarea } from '$lib/components/ui/textarea';
	import Card from '../components/Card.svelte';
	import Field from '../components/Field.svelte';
	import Toggle from '../components/Toggle.svelte';
	import { api, pumpkin } from '../store.svelte';
	import type { Settings } from '../types';
	import LlamaCard from './LlamaCard.svelte';
	import { onMount } from 'svelte';

	interface UpdateInfo {
		available: boolean;
		checked: number | null;
		current: string;
		failed: boolean;
		latest: string;
		notes: string;
		url: string;
	}

	let s = $state<Settings | null>(null);
	let dirsText = $state('');
	let hfToken = $state('');
	let upd = $state<UpdateInfo | null>(null);
	let updating = $state(false);
	let svc = $state<{ active: boolean; available: boolean; installed: boolean; linger: boolean; linger_command: string } | null>(null);

	onMount(async () => {
		s = await pumpkin.run(() => api<Settings>('settings'));
		dirsText = (s?.models_dirs ?? []).join('\n');
		svc = await api<typeof svc>('service').catch(() => null);
		upd = await api<UpdateInfo>('update').catch(() => null);
	});

	async function checkNow() {
		upd = (await pumpkin.run(() => api<UpdateInfo>('update?fresh=1'))) ?? upd;

		if (upd && !upd.available && !upd.failed) pumpkin.say('You have the latest version');
	}

	async function applyUpdate() {
		if (!confirm('Update Pumpkin now? It restarts, and the model stops while it does.')) return;

		updating = true;
		await api('update/apply', { body: {} }).catch(() => {});

		// the server goes away and comes back as the new version
		const was = pumpkin.state?.version;
		const timer = setInterval(async () => {
			const r = await api<{ version: string }>('state').catch(() => null);

			if (r && r.version !== was) {
				clearInterval(timer);
				location.reload();
			}
		}, 2500);
	}

	async function save(patch: Partial<Settings>, msg = 'Saved') {
		const r = await pumpkin.run(() => api<Settings>('settings', { body: patch, method: 'PUT' }), msg);

		if (r) s = r;
	}

	async function service(action: 'install' | 'uninstall') {
		svc = await pumpkin.run(() => api<typeof svc>(`service/${action}`, { body: {} }), action === 'install' ? 'Pumpkin will start when you log in' : 'Removed');
	}

	async function quit() {
		if (!confirm('Stop Pumpkin and the model? Open it again from the app menu or with the pumpkin command.')) return;

		await api('quit', { body: {} }).catch(() => {});
		document.body.innerHTML = '<p style="font:16px system-ui;padding:3rem;text-align:center">Pumpkin has stopped.</p>';
	}

	async function resetKey() {
		if (!confirm('Make a new access key? Phones and browsers signed in with the old one will need the new link.')) return;

		await pumpkin.run(() => api('token/reset', { body: {} }), 'New key made');
		location.href = './login';
	}
</script>

<div class="flex flex-col gap-4">
	<LlamaCard />

	{#if !s}
		<div class="flex justify-center py-8"><LoaderCircle class="h-5 w-5 animate-spin text-muted-foreground" /></div>
	{:else}
		<Card description="How the chat behaves: sampling, display, tools and more." title="Chat settings">
			<Button class="rounded-full" onclick={() => window.dispatchEvent(new CustomEvent('pumpkin:chat-settings'))} size="sm" variant="secondary">Open chat settings</Button>
		</Card>

		<Card title="Folders">
			<div class="flex flex-col gap-4">
				<Field hint="One folder per line. Pumpkin finds .gguf files in them and their subfolders." label="Model folders">
					<Textarea bind:value={dirsText} class="min-h-20 font-mono text-xs" />
				</Field>
				<Field label="Download new models to"><Input bind:value={s.download_dir} class="font-mono text-xs" /></Field>
				<Field label="Save task reports in"><Input bind:value={s.reports_dir} class="font-mono text-xs" /></Field>
				<div>
					<Button class="rounded-full" onclick={() => save({ download_dir: s!.download_dir, models_dirs: dirsText.split('\n').map((x) => x.trim()).filter(Boolean), reports_dir: s!.reports_dir })} size="sm">Save folders</Button>
				</div>
			</div>
		</Card>

		<Card title="Behaviour">
			<div class="flex flex-col gap-2">
				<Toggle bind:checked={s.keep_awake} description="Stops the computer from suspending while a model is loaded. The screen can still lock." label="Keep the computer awake" onchange={(v) => save({ keep_awake: v })} />
				<Toggle bind:checked={s.autostart_model} description="Starts the last preset when Pumpkin starts." label="Start the last model automatically" onchange={(v) => save({ autostart_model: v })} />
				<Toggle bind:checked={s.notifications} description="Desktop notifications when tasks finish." label="Notifications" onchange={(v) => save({ notifications: v })} />
				{#if svc?.available}
					<Toggle checked={svc.installed} description="Runs Pumpkin in the background from login, so tasks and phone access work with the window closed." label="Start Pumpkin when I log in" onchange={(v) => service(v ? 'install' : 'uninstall')} />
					{#if svc.installed && !svc.linger}
						<p class="text-xs text-muted-foreground">To keep it running while you are logged out, run <code>{svc.linger_command}</code> once.</p>
					{/if}
				{/if}
			</div>
		</Card>

		<Card title="Performance and network">
			<div class="grid gap-4 sm:grid-cols-3">
				<Field hint="0 = number of physical cores" label="CPU threads"><Input bind:value={s.threads} min="0" type="number" /></Field>
				<Field hint="Restart Pumpkin after changing" label="Pumpkin port"><Input bind:value={s.port} type="number" /></Field>
				<Field hint="llama-server, local only" label="Model server port"><Input bind:value={s.llama_port} type="number" /></Field>
			</div>
			<div class="mt-4"><Button class="rounded-full" onclick={() => save({ llama_port: Number(s!.llama_port), port: Number(s!.port), threads: Number(s!.threads) })} size="sm">Save</Button></div>
		</Card>

		<Card description="Only needed for models that ask you to accept a license on huggingface.co." title="Hugging Face token">
			<div class="flex gap-2">
				<Input autocomplete="off" bind:value={hfToken} placeholder={s.hf_token ? 'A token is saved' : 'hf_…'} type="password" />
				<Button class="shrink-0 rounded-full" disabled={!hfToken} onclick={() => save({ hf_token: hfToken }).then(() => (hfToken = ''))} size="sm">Save</Button>
				{#if s.hf_token}<Button class="shrink-0 rounded-full" onclick={() => save({ hf_token: '' })} size="sm" variant="ghost">Remove</Button>{/if}
			</div>
		</Card>

		<Card title="Updates">
			<div class="flex flex-col gap-3 text-sm">
				<p class="text-muted-foreground">
					Version {pumpkin.state?.version}.
					{#if upd?.available}
						<span class="font-medium text-foreground">Version {upd.latest} is out.</span>
					{:else if upd?.failed}
						Could not reach GitHub to check.
					{:else if upd?.checked}
						This is the latest version.
					{/if}
				</p>
				{#if upd?.available && upd.notes}<p class="rounded-xl bg-muted/60 p-3 text-xs whitespace-pre-wrap">{upd.notes}</p>{/if}
				<div class="flex flex-wrap gap-2">
					{#if upd?.available}
						<Button class="rounded-full" disabled={updating} onclick={applyUpdate} size="sm">
							{#if updating}<LoaderCircle class="h-4 w-4 animate-spin" /> Updating{:else}Update to {upd.latest}{/if}
						</Button>
					{/if}
					<Button class="rounded-full" disabled={updating} onclick={checkNow} size="sm" variant="secondary">Check now</Button>
				</div>
				<Toggle bind:checked={s.check_updates} description="Asks GitHub twice a day whether a new version exists. Nothing else is sent." label="Check for updates automatically" onchange={(v) => save({ check_updates: v })} />
			</div>
		</Card>

		<Card description="What Pumpkin sends over the network, and when." title="Privacy">
			<ul class="flex flex-col gap-2.5 text-sm">
				<li><span class="font-medium">Your chats, files and prompts</span> stay on this computer. The model server only listens on 127.0.0.1.</li>
				<li><span class="font-medium">Hugging Face</span>: when you search for models, open Recommended, or download. Your search words go there, and your token if you saved one.</li>
				<li><span class="font-medium">GitHub</span>: to check for a new Pumpkin (if the setting above is on) and to download llama.cpp when you ask.</li>
				<li><span class="font-medium">Web search</span>: only when a model uses its search tool. The search words go to DuckDuckGo, and the pages the model opens are fetched.</li>
				<li><span class="font-medium">ntfy</span>: only if you set a topic. The notification text goes to the ntfy server.</li>
				<li><span class="font-medium">Tailscale</span>: only if you turn on phone access.</li>
				<li class="text-muted-foreground">No accounts, no analytics, no crash reports.</li>
			</ul>
		</Card>

		<Card title="Pumpkin">
			<p class="text-sm text-muted-foreground">Free and open source under the MIT license.</p>
			<div class="mt-3 flex flex-wrap gap-2">
				<Button class="rounded-full" href="#/pumpkin/setup" size="sm" variant="secondary">Run setup again</Button>
				<Button class="rounded-full" onclick={resetKey} size="sm" variant="ghost">New access key</Button>
				<Button class="rounded-full" onclick={quit} size="sm" variant="ghost">Quit Pumpkin</Button>
			</div>
		</Card>
	{/if}
</div>
