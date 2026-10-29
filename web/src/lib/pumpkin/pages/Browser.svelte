<script lang="ts">
	// Browser: Pumpkin working in your own Chrome or Edge. The chat itself is in the extension's side panel (that is
	// where the pages are); this page sets it up, shows who is paired and which sites are allowed, and keeps the
	// record of what past browser chats did.
	import { ChevronDown, ChevronRight, LoaderCircle, Trash2 } from '@lucide/svelte';
	import { Button } from '$lib/components/ui/button';
	import Card from '../components/Card.svelte';
	import { api, pumpkin } from '../store.svelte';
	import BrowserCard from './BrowserCard.svelte';
	import { onDestroy, onMount } from 'svelte';

	interface Summary {
		id: string;
		state: string;
		title: string;
		updated: number;
	}

	interface Ev {
		allow?: string;
		args?: Record<string, unknown>;
		kind: string;
		n: number;
		name?: string;
		ok?: boolean;
		text?: string;
	}

	let chats = $state<Summary[] | null>(null);
	let open = $state<string | null>(null);
	let events = $state<Ev[]>([]);
	let loading = $state(false);
	let timer: ReturnType<typeof setInterval> | undefined;

	async function load() {
		const r = await api<{ sessions: Summary[] }>('browser/sessions').catch(() => null);

		if (r) chats = r.sessions;
	}

	onMount(() => {
		void load();
		timer = setInterval(load, 8000);
	});

	onDestroy(() => clearInterval(timer));

	async function show(c: Summary) {
		if (open === c.id) {
			open = null;

			return;
		}

		open = c.id;
		loading = true;
		events = [];
		const r = await api<{ events: Ev[] }>(`browser/sessions/${c.id}`).catch(() => null);

		loading = false;
		events = r?.events ?? [];
	}

	async function remove(c: Summary) {
		if (!confirm('Delete this browser chat from Pumpkin?')) return;

		await pumpkin.run(() => api(`browser/sessions/${c.id}`, { method: 'DELETE', body: {} }), 'Deleted');

		if (open === c.id) open = null;

		await load();
	}

	const VERB: Record<string, string> = {
		back: 'Went back',
		choose: 'Chose',
		click: 'Clicked',
		close_tab: 'Closed a tab',
		find: 'Looked for',
		goto: 'Went to',
		open_tab: 'Opened',
		press: 'Pressed',
		read_page: 'Read the page',
		scroll: 'Scrolled',
		tabs: 'Looked at the tabs',
		type: 'Typed',
		wait: 'Waited'
	};

	function step(e: Ev) {
		const a = e.args ?? {};
		const what = String(a.url ?? a.query ?? a.text ?? a.option ?? a.key ?? a.direction ?? '').slice(0, 80);

		return `${VERB[e.name ?? ''] ?? e.name}${what ? ' ' + what : ''}${e.ok === false ? ' (failed)' : ''}`;
	}

	function ago(t: number) {
		const s = Math.max(0, Date.now() / 1000 - t);

		if (s < 60) return 'just now';
		if (s < 3600) return `${Math.round(s / 60)} min ago`;
		if (s < 86400) return `${Math.round(s / 3600)} h ago`;

		return new Date(t * 1000).toLocaleDateString();
	}
</script>

<div class="flex flex-col gap-4">
	<Card
		description="The model on your computer reads pages, clicks and types in your own browser, with your logins and your say-so."
		title="Pumpkin in your browser"
	>
		<ol class="list-decimal space-y-1 pl-5 text-sm text-muted-foreground">
			<li>Set up the extension below (once), then click its icon in the toolbar to open the Pumpkin panel.</li>
			<li>Keep the panel open and type the job there ("find the cheapest one on this page"). It starts in the tab you are looking at.</li>
			<li>It asks you before using a site, and before each action if you chose the careful mode. Everything it did is kept below.</li>
		</ol>
	</Card>

	<BrowserCard />

	<Card description="What past browser chats did, step by step. They stay on this computer." title="Recent browser chats">
		{#if chats === null}
			<div class="flex justify-center py-4"><LoaderCircle class="h-5 w-5 animate-spin text-muted-foreground" /></div>
		{:else if !chats.length}
			<p class="text-sm text-muted-foreground">Nothing yet. Start one in the extension's panel.</p>
		{:else}
			<ul class="flex flex-col gap-2">
				{#each chats as c (c.id)}
					<li class="rounded-xl bg-muted/40">
						<div class="flex items-center gap-2 px-3 py-2">
							<button class="flex min-w-0 flex-1 items-center gap-2 text-left text-sm" onclick={() => show(c)}>
								{#if open === c.id}<ChevronDown class="h-4 w-4 shrink-0" />{:else}<ChevronRight class="h-4 w-4 shrink-0" />{/if}
								<span class="min-w-0">
									<span class="block truncate">{c.title}</span>
									<span class="text-xs text-muted-foreground">
										{ago(c.updated)}{c.state === 'running' ? ' · working' : c.state === 'waiting' ? ' · waiting for you' : c.state === 'error' ? ' · stopped with an error' : ''}
									</span>
								</span>
							</button>
							<Button aria-label="Delete this chat" class="size-8 shrink-0 rounded-full" onclick={() => remove(c)} size="icon" variant="ghost">
								<Trash2 class="h-4 w-4" />
							</Button>
						</div>

						{#if open === c.id}
							<div class="flex flex-col gap-1.5 border-t border-border/60 px-3 py-2 text-sm">
								{#if loading}
									<LoaderCircle class="h-4 w-4 animate-spin text-muted-foreground" />
								{:else}
									{#each events as e (e.n)}
										{#if e.kind === 'user'}
											<p class="rounded-lg bg-background px-2 py-1 break-words whitespace-pre-wrap">{e.text}</p>
										{:else if e.kind === 'assistant' && e.text?.trim()}
											<p class="break-words whitespace-pre-wrap">{e.text}</p>
										{:else if e.kind === 'tool'}
											<p class={['text-xs break-words', e.ok === false ? 'text-destructive' : 'text-muted-foreground']}>{step(e)}</p>
										{:else if e.kind === 'answer' && e.allow === 'no'}
											<p class="text-xs text-muted-foreground">You said no.</p>
										{:else if e.kind === 'error'}
											<p class="text-xs break-words text-destructive">{e.text}</p>
										{/if}
									{/each}
								{/if}
							</div>
						{/if}
					</li>
				{/each}
			</ul>
		{/if}
	</Card>
</div>
