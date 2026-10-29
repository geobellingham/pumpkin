<script lang="ts">
	import { LoaderCircle } from '@lucide/svelte';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Textarea } from '$lib/components/ui/textarea';
	import Card from '../components/Card.svelte';
	import Field from '../components/Field.svelte';
	import Segmented from '../components/Segmented.svelte';
	import PumpkinFolder from '../components/PumpkinFolder.svelte';
	import { settingsStore } from '$lib/stores/settings/index.svelte';
	import Toggle from '../components/Toggle.svelte';
	import { api, pumpkin } from '../store.svelte';
	import type { Settings } from '../types';
	import LlamaCard from './LlamaCard.svelte';
	import SignIns from './SignIns.svelte';
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
	let stuck = $state<string | null>(null);
	let progress = $state('');
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
		stuck = null;

		try {
			await api('update/apply', { body: {} });
		} catch (e) {
			updating = false;
			pumpkin.say(e instanceof Error ? e.message : String(e), 'error');

			return;
		}

		// the server goes away and comes back as the new version. The page reloads as soon as it answers again after
		// having been away, or answers with another version; after a few minutes it gives up and says why
		const was = upd?.current ?? pumpkin.state?.version;
		const started = Date.now();
		let down = false;
		progress = 'Starting the update';
		const timer = setInterval(async () => {
			const r = await api<{ version: string }>('state').catch(() => null);

			if (!r) down = true;

			if (r && (down || (was && r.version !== was))) {
				clearInterval(timer);
				location.reload();

				return;
			}

			// what the installer says, so a slow or stuck update shows where it is
			const l = await api<{ log: string }>('update/log').catch(() => null);
			const last = (l?.log ?? '').split('\n').map((x) => x.trim()).filter(Boolean).pop();

			if (last) progress = last;

			if (Date.now() - started > 4 * 60_000) {
				clearInterval(timer);
				updating = false;
				progress = '';
				stuck = l?.log ?? (await api<{ log: string }>('update/log').catch(() => null))?.log ?? '';
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

		{#if pumpkin.state?.platform !== 'android'}<PumpkinFolder />{/if}

		<Card description="For the chat and Pumpkin's pages, on this device. The button at the top of Pumpkin's pages switches it too." title="Appearance">
			<Segmented
				onchange={(v) => settingsStore.updateTheme(v)}
				options={[
					{ label: 'Same as the system', value: 'system' },
					{ label: 'Light', value: 'light' },
					{ label: 'Dark', value: 'dark' }
				]}
				value={String(settingsStore.config.theme ?? 'system')}
			/>
		</Card>

		<Card title="Behaviour">
			<div class="flex flex-col gap-2">
				<Toggle bind:checked={s.keep_awake} description="Stops the computer from suspending while a model is loaded. The screen can still lock." label="Keep the computer awake" onchange={(v) => save({ keep_awake: v })} />
				<Toggle bind:checked={s.keep_context} description="When a model stops or is swapped out (to draw a picture, say), Pumpkin saves the conversation it has read and loads it back at the next start, so a long chat does not have to be read again. The files are as big as the context and only the newest few are kept." label="Remember the conversation across restarts" onchange={(v) => save({ keep_context: v })} />
				<Toggle bind:checked={s.compact_chat} description="When a chat grows past what the model can hold, Pumpkin sends it a summary of the older messages and the newest ones in full. The chat on screen keeps every message. Without this the model stops with a 'context is full' error." label="Summarise long chats instead of stopping" onchange={(v) => save({ compact_chat: v })} />
				<Toggle bind:checked={s.vision_relay} description="When the chat model cannot see pictures, a small vision model you have installed looks at them and tells it what is there, text included. It loads only when needed and frees its memory after a few idle minutes." label="Let text-only models see pictures" onchange={(v) => save({ vision_relay: v })} />
				<Toggle bind:checked={s.autostart_model} description="Starts the last preset when Pumpkin starts." label="Start the last model automatically" onchange={(v) => save({ autostart_model: v })} />
				<Toggle bind:checked={s.web_private} description="Off: when the model opens a web page it can only reach the public internet, never this computer or your home or office network (a web page could otherwise send it there). Turn it on only if you want it to read pages on your own network. Restart the model to apply it." label="Let the model read private addresses" onchange={(v) => save({ web_private: v })} />
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
					{#if upd?.available && pumpkin.state?.platform === 'android'}
						<Button class="rounded-full" href={upd.url} size="sm" target="_blank">Get the new APK</Button>
					{:else if upd?.available}
						<Button class="rounded-full" disabled={updating} onclick={applyUpdate} size="sm">
							{#if updating}<LoaderCircle class="h-4 w-4 animate-spin" /> Updating{:else}Update to {upd.latest}{/if}
						</Button>
					{/if}
					<Button class="rounded-full" disabled={updating} onclick={checkNow} size="sm" variant="secondary">Check now</Button>
				</div>
				{#if updating && progress}
					<p class="text-xs text-muted-foreground">{progress}</p>
				{/if}
				{#if stuck !== null}
					<div class="rounded-xl border border-destructive/40 p-3 text-xs">
						<p class="mb-2 text-sm">The update did not finish. Run this in a terminal instead:</p>
						<pre class="overflow-x-auto rounded-lg bg-muted/60 p-2 font-mono">curl -fsSL https://raw.githubusercontent.com/geobellingham/pumpkin/main/install.sh | bash</pre>
						{#if stuck}<details class="mt-2"><summary class="cursor-pointer text-muted-foreground">What happened</summary><pre class="mt-1 max-h-48 overflow-auto font-mono whitespace-pre-wrap">{stuck}</pre></details>{/if}
					</div>
				{/if}
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

		<SignIns />

		<Card title="Pumpkin">
			<p class="text-sm text-muted-foreground">Free to use and change under the MIT license with the Commons Clause: you may not sell it.</p>
			<div class="mt-3 flex flex-wrap gap-2">
				<Button class="rounded-full" href="#/pumpkin/setup" size="sm" variant="secondary">Run setup again</Button>
				{#if pumpkin.state?.platform !== 'android'}
					<Button class="rounded-full" onclick={quit} size="sm" variant="ghost">Quit Pumpkin</Button>
				{/if}
			</div>
		</Card>
	{/if}
</div>
