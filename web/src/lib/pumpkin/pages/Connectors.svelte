<script lang="ts">
	// Connectors: ready-made MCP servers for apps and services. Fill in a token or a folder, test it, switch it on;
	// the model gets its tools at the next start. The chat asks before each tool call.
	import { CircleAlert, CircleCheck, ExternalLink, LoaderCircle, Plug, RotateCw, Trash2 } from '@lucide/svelte';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Switch } from '$lib/components/ui/switch';
	import Card from '../components/Card.svelte';
	import Field from '../components/Field.svelte';
	import Toggle from '../components/Toggle.svelte';
	import { api, pumpkin } from '../store.svelte';
	import type { Connector } from '../types';
	import { onMount } from 'svelte';

	let items = $state<Connector[] | null>(null);
	let openId = $state<string | null>(null);
	let draft = $state<Record<string, unknown>>({});
	let busy = $state<string | null>(null);
	let restart = $state(false);

	async function load() {
		items = (await pumpkin.run(() => api<{ items: Connector[] }>('connectors')))?.items ?? [];
	}

	onMount(load);

	function setup(c: Connector) {
		if (openId === c.id) {
			openId = null;

			return;
		}

		openId = c.id;
		draft = Object.fromEntries(
			c.fields.map((f) => [f.id, f.kind === 'paths' ? ((c.values[f.id] as string[] | null) ?? []).join('\n') : f.kind === 'secret' ? (c.values[f.id] as string) : c.values[f.id] ?? ''])
		);
	}

	function replace(c: Connector) {
		items = (items ?? []).map((x) => (x.id === c.id ? c : x));
	}

	async function save(c: Connector, enabled?: boolean) {
		busy = c.id;

		const r = await pumpkin.run(() =>
			api<{ item: Connector; restart_needed: boolean }>(`connectors/${c.id}`, { body: { enabled, values: openId === c.id ? draft : undefined } })
		);

		busy = null;

		if (!r) return;

		replace(r.item);
		restart = restart || r.restart_needed;

		if (enabled === undefined) pumpkin.say('Saved');
	}

	async function test(c: Connector) {
		await save(c);
		busy = c.id;
		pumpkin.fast++;

		const r = await pumpkin.run(() => api<Connector['test']>(`connectors/${c.id}/test`, { body: {} }));

		pumpkin.fast--;
		busy = null;

		if (r) replace({ ...(items ?? []).find((x) => x.id === c.id)!, test: r });
	}

	async function remove(c: Connector) {
		const r = await pumpkin.run(() => api<{ restart_needed: boolean }>(`connectors/${c.id}/remove`, { body: {} }), `Removed ${c.name}`);

		restart = restart || !!r?.restart_needed;
		openId = null;
		await load();
	}

	async function restartModel() {
		const id = pumpkin.state?.server.preset?.id;

		if (id) await pumpkin.startPreset(id);

		restart = false;
	}

	const sorted = $derived([...(items ?? [])].sort((a, b) => Number(b.added) - Number(a.added)));
</script>

<div class="flex flex-col gap-4">
	<Card description="Ready-made tool servers for apps and services. Fill in what one needs, test it, then switch it on. A model gets the tools of the connectors that are on when it starts, and the chat asks you before each tool call." title="Connectors">
		{#if restart && pumpkin.state?.server.preset}
			<div class="mb-3 flex flex-wrap items-center gap-2 rounded-xl bg-muted/60 p-3 text-sm">
				<span class="min-w-0 flex-1">Restart the model to give it the new tools.</span>
				<Button class="rounded-full" onclick={restartModel} size="sm"><RotateCw class="h-4 w-4" /> Restart {pumpkin.state.server.preset.name}</Button>
			</div>
		{/if}

		{#if items === null}
			<div class="flex justify-center py-6"><LoaderCircle class="h-5 w-5 animate-spin text-muted-foreground" /></div>
		{:else}
			<ul class="flex flex-col divide-y divide-border/60">
				{#each sorted as c (c.id)}
					<li class="py-3">
						<div class="flex items-start gap-3">
							<span class="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-lg bg-muted"><Plug class="size-4 text-muted-foreground" /></span>
							<div class="min-w-0 flex-1">
								<div class="flex flex-wrap items-center gap-x-2 gap-y-0.5">
									<span class="text-sm font-medium">{c.name}</span>
									{#if !c.available}
										<a class="text-[11px] text-amber-700 underline dark:text-amber-400" href={c.needs_url} rel="noreferrer" target="_blank">Needs {c.needs_name}</a>
									{:else if c.enabled && c.test?.ok}
										<span class="text-[11px] text-emerald-700 dark:text-emerald-400">On, {c.test.tools.length} tools</span>
									{:else if c.enabled}
										<span class="text-[11px] text-emerald-700 dark:text-emerald-400">On</span>
									{:else if c.added && !c.missing.length}
										<span class="text-[11px] text-muted-foreground">Set up, off</span>
									{/if}
								</div>
								<p class="text-xs text-muted-foreground">{c.blurb}</p>
							</div>
							<div class="flex shrink-0 items-center gap-2">
								{#if c.added && !c.missing.length}
									<Switch aria-label="Use {c.name}" checked={c.enabled} disabled={busy === c.id} onCheckedChange={(v) => save(c, v)} />
								{/if}
								<Button class="rounded-full" onclick={() => setup(c)} size="sm" variant={openId === c.id ? 'secondary' : 'ghost'}>{c.added ? 'Settings' : 'Set up'}</Button>
							</div>
						</div>

						{#if openId === c.id}
							<div class="mt-3 ml-11 flex flex-col gap-3 rounded-xl border border-border/60 p-3">
								{#each c.fields as f (f.id)}
									{#if f.kind === 'toggle'}
										<Toggle checked={!!draft[f.id]} description={f.help} label={f.label} onchange={(v) => (draft[f.id] = v)} />
									{:else if f.kind === 'paths'}
										<Field hint={f.help} label={f.label + (f.required ? '' : ' (optional)')}>
											<textarea bind:value={draft[f.id] as string} class="min-h-20 rounded-xl border border-input bg-transparent p-2 font-mono text-xs dark:bg-input/30" placeholder="/home/you/Documents"></textarea>
										</Field>
									{:else}
										<Field hint={f.help} label={f.label}>
											<Input
												autocomplete="off"
												bind:value={draft[f.id] as string}
												onfocus={() => f.kind === 'secret' && draft[f.id] === 'set' && (draft[f.id] = '')}
												placeholder={f.kind === 'secret' && c.values[f.id] === 'set' ? 'Saved. Type to replace it.' : (f.placeholder ?? '')}
												type={f.kind === 'secret' ? 'password' : 'text'}
											/>
										</Field>
									{/if}
								{/each}

								{#if c.test}
									<p class={['flex items-start gap-2 rounded-lg p-2 text-xs', c.test.ok ? 'bg-emerald-500/10 text-emerald-800 dark:text-emerald-300' : 'bg-destructive/10 text-destructive']}>
										{#if c.test.ok}<CircleCheck class="mt-0.5 size-4 shrink-0" />{:else}<CircleAlert class="mt-0.5 size-4 shrink-0" />{/if}
										<span class="min-w-0 break-words">
											{c.test.ok ? `Works. ${c.test.tools.length} tools: ${c.test.tools.slice(0, 12).join(', ')}${c.test.tools.length > 12 ? ', …' : ''}` : c.test.error}
										</span>
									</p>
								{/if}

								<div class="flex flex-wrap items-center gap-2">
									<Button class="rounded-full" disabled={busy === c.id} onclick={() => save(c)} size="sm">Save</Button>
									<Button class="rounded-full" disabled={busy === c.id || !c.available} onclick={() => test(c)} size="sm" variant="secondary">
										{#if busy === c.id}<LoaderCircle class="h-4 w-4 animate-spin" />{/if} Test
									</Button>
									<a class="inline-flex items-center gap-1 text-xs text-muted-foreground underline" href={c.docs} rel="noreferrer" target="_blank">About it <ExternalLink class="size-3" /></a>
									{#if c.added}
										<Button class="ml-auto rounded-full text-muted-foreground" onclick={() => remove(c)} size="sm" variant="ghost"><Trash2 class="h-4 w-4" /> Remove</Button>
									{/if}
								</div>
								{#if busy === c.id}<p class="text-xs text-muted-foreground">The first test downloads it, which can take a minute or two.</p>{/if}
							</div>
						{/if}
					</li>
				{/each}
			</ul>
		{/if}
	</Card>

	<p class="px-1 text-xs text-muted-foreground">
		Tokens stay on this computer, in Pumpkin's settings folder. Give each one only the access you want the model to have. Your own MCP servers can still be added by hand on the Agent page.
	</p>
</div>
