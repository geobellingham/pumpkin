<script lang="ts">
	import { LoaderCircle, X } from '@lucide/svelte';
	import { Button } from '$lib/components/ui/button';
	import Card from '../components/Card.svelte';
	import { api, pumpkin } from '../store.svelte';
	import { onMount } from 'svelte';

	interface Session {
		created: number;
		current: boolean;
		id: string;
		ip: string;
		last_used: number;
		local: boolean;
		name: string;
	}

	let list = $state<Session[] | null>(null);

	async function load() {
		const r = await pumpkin.run(() => api<{ sessions: Session[] }>('sessions'));

		if (r) list = r.sessions;
	}

	onMount(load);

	async function remove(x: Session) {
		const r = await pumpkin.run(() => api<{ sessions: Session[] }>(`sessions/${x.id}`, { method: 'DELETE' }), 'Signed out');

		if (r) list = r.sessions;
	}

	async function clearOthers() {
		if (!confirm('Sign out every other browser and phone? They can sign in again with the access key.')) return;

		const r = await pumpkin.run(() => api<{ sessions: Session[] }>('sessions/clear', { body: { keep_this: true } }), 'Signed out everywhere else');

		if (r) list = r.sessions;
	}

	async function newKey() {
		if (!confirm('Make a new access key? Old sign-in links and QR codes stop working. Browsers and phones that are signed in stay signed in, and you can remove them here.')) return;

		await pumpkin.run(() => api('token/reset', { body: {} }), 'New access key made');
	}

	function when(t: number) {
		const s = Math.max(0, Date.now() / 1000 - t);

		if (s < 120) return 'active now';
		if (s < 3600) return `${Math.round(s / 60)} min ago`;
		if (s < 86400) return `${Math.round(s / 3600)} h ago`;

		return new Date(t * 1000).toLocaleDateString();
	}

	const others = $derived((list ?? []).filter((x) => !x.current).length);
</script>

<Card description="Every browser and phone that signed in has its own key. Lost a phone? Sign it out here." title="Signed-in devices">
	{#if list === null}
		<div class="flex justify-center py-4"><LoaderCircle class="h-5 w-5 animate-spin text-muted-foreground" /></div>
	{:else if !list.length}
		<p class="text-sm text-muted-foreground">Nothing signed in yet besides this computer's own window.</p>
	{:else}
		<ul class="flex flex-col divide-y divide-border/60 rounded-xl border border-border/60">
			{#each list as x (x.id)}
				<li class="flex items-center gap-3 px-3 py-2.5">
					<div class="min-w-0 flex-1">
						<div class="truncate text-sm font-medium">
							{x.name}{#if x.current}<span class="ml-1 font-normal text-muted-foreground">(this one)</span>{:else if x.local}<span class="ml-1 font-normal text-muted-foreground">(this computer)</span>{/if}
						</div>
						<div class="truncate text-xs text-muted-foreground">{x.local ? 'On this computer' : x.ip}, {when(x.last_used)}</div>
					</div>
					{#if !x.current}
						<Button aria-label="Sign out {x.name}" class="size-8 rounded-full" onclick={() => remove(x)} size="icon" variant="ghost">
							<X class="h-4 w-4" />
						</Button>
					{/if}
				</li>
			{/each}
		</ul>
	{/if}

	<div class="mt-3 flex flex-wrap gap-2">
		{#if others}
			<Button class="rounded-full" onclick={clearOthers} size="sm" variant="secondary">Sign out all others</Button>
		{/if}
		<Button class="rounded-full" onclick={newKey} size="sm" variant="ghost">New access key</Button>
	</div>
</Card>
