<script lang="ts">
	// One artifact: what it looks like, its text, editing, earlier versions. A page or diagram the model wrote is shown
	// in a frame that has no network and no access to Pumpkin (sandbox without allow-same-origin, plus the policy the
	// server puts in the page), so even a hostile page can only draw.
	import { Copy, Download, LoaderCircle, MessageSquare, RotateCcw, Save, Trash } from '@lucide/svelte';
	import { MarkdownContent } from '$lib/components/app';
	import { Button } from '$lib/components/ui/button';
	import { api, pumpkin } from '../store.svelte';
	import Segmented from './Segmented.svelte';
	import { onDestroy, onMount } from 'svelte';

	interface Full {
		by: string;
		content: string;
		doc: string | null;
		history: { at: number; by: string; n: number; note: string }[];
		id: string;
		kind: string;
		shown: number;
		title: string;
		updated: number;
		version: number;
	}

	interface Props {
		ondeleted?: () => void;
		id: string;
		tall?: boolean;
	}

	let { id, ondeleted, tall = false }: Props = $props();
	let art = $state<Full | null>(null);
	let view = $state('preview');
	let draft = $state('');
	let editing = $state(false);
	let version = $state<number | null>(null);
	let missing = $state(false);
	let timer: ReturnType<typeof setInterval> | undefined;
	let loadedId = '';

	async function load(force = false) {
		if (editing && !force) return;

		try {
			const r = await api<Full>(`artifacts/${id}` + (version ? `?version=${version}` : ''));

			if (r.id !== id) return;

			const fresh = !art || art.id !== r.id || art.updated !== r.updated || art.shown !== r.shown;

			if (fresh) art = r;

			missing = false;
		} catch {
			missing = true;
		}
	}

	$effect(() => {
		if (id !== loadedId) {
			loadedId = id;
			art = null;
			version = null;
			editing = false;
			view = 'preview';
			void load(true);
		}
	});

	onMount(() => {
		timer = setInterval(() => document.visibilityState === 'visible' && load(), 3000);
	});

	onDestroy(() => clearInterval(timer));

	const previewable = $derived(!!art && ['html', 'svg', 'md'].includes(art.kind));
	const isOld = $derived(!!art && art.shown !== art.version);

	$effect(() => {
		if (art && !previewable && view === 'preview') view = 'code';
	});

	function startEdit() {
		if (!art) return;

		draft = art.content;
		editing = true;
		view = 'code';
	}

	async function save() {
		if (!art) return;

		if (!(await pumpkin.run(() => api(`artifacts/${art!.id}`, { body: { content: draft }, method: 'PUT' }), 'Saved'))) return;

		editing = false;
		version = null;
		await load(true);
	}

	async function pick(n: string) {
		version = n ? Number(n) : null;
		editing = false;
		await load(true);
	}

	async function restore() {
		if (!art) return;

		if (await pumpkin.run(() => api(`artifacts/${art!.id}/restore`, { body: { version: art!.shown } }), 'Went back to that version')) {
			version = null;
			await load(true);
		}
	}

	async function remove() {
		if (!art || !confirm(`Delete “${art.title}” and all its versions?`)) return;

		if (await pumpkin.run(() => api(`artifacts/${art!.id}`, { method: 'DELETE' }), 'Deleted')) ondeleted?.();
	}

	async function copy() {
		if (!art) return;

		try {
			await navigator.clipboard.writeText(art.content);
			pumpkin.say('Copied');
		} catch {
			/* copying is a nicety */
		}
	}

	function download() {
		if (!art) return;

		const ext = { code: 'txt', html: 'html', md: 'md', svg: 'svg', text: 'txt' }[art.kind] ?? 'txt';
		const a = document.createElement('a');
		const url = URL.createObjectURL(new Blob([art.content], { type: 'text/plain;charset=utf-8' }));

		a.href = url;
		a.download = `${art.title.replace(/[^\w .-]+/g, '').trim() || 'artifact'}.${ext}`;
		a.click();
		setTimeout(() => URL.revokeObjectURL(url), 1000);
	}

	function ask() {
		if (!art) return;

		pumpkin.sendToChat(`About the artifact “${art.title}” (${art.id}): read it with artifacts_read first, then `);
	}

	const when = (t: number) => new Date(t * 1000).toLocaleString([], { dateStyle: 'short', timeStyle: 'short' });
</script>

{#if missing && !art}
	<p class="p-4 text-sm text-muted-foreground">This artifact is gone.</p>
{:else if !art}
	<div class="flex justify-center py-10"><LoaderCircle class="h-5 w-5 animate-spin text-muted-foreground" /></div>
{:else}
	<div class="flex min-h-0 flex-1 flex-col gap-2">
		<div class="flex flex-wrap items-center gap-2">
			{#if previewable}
				<Segmented bind:value={view} options={[{ label: 'Preview', value: 'preview' }, { label: art.kind === 'md' ? 'Text' : 'Code', value: 'code' }]} />
			{/if}

			{#if art.history.length > 1}
				<select
					aria-label="Version"
					class="h-8 rounded-lg border border-input bg-transparent px-2 text-xs dark:bg-input/30"
					onchange={(e) => pick((e.currentTarget as HTMLSelectElement).value)}
					value={version ? String(version) : ''}
				>
					<option class="bg-popover text-popover-foreground" value="">Newest (v{art.version})</option>
					{#each art.history.slice().reverse().slice(1) as h (h.n)}
						<option class="bg-popover text-popover-foreground" value={String(h.n)}>v{h.n} · {h.by === 'you' ? 'you' : 'model'} · {when(h.at)}</option>
					{/each}
				</select>
			{/if}

			<span class="flex-1"></span>

			{#if isOld}
				<Button class="h-8 rounded-full" onclick={restore} size="sm" variant="outline"><RotateCcw class="mr-1 h-3.5 w-3.5" />Use this version</Button>
			{:else if editing}
				<Button class="h-8 rounded-full" onclick={save} size="sm"><Save class="mr-1 h-3.5 w-3.5" />Save</Button>
				<Button class="h-8 rounded-full" onclick={() => (editing = false)} size="sm" variant="ghost">Cancel</Button>
			{:else}
				<Button class="h-8 rounded-full" onclick={startEdit} size="sm" variant="outline">Edit</Button>
				<Button class="h-8 rounded-full" onclick={ask} size="sm" title="Puts a request in the chat box" variant="outline"><MessageSquare class="mr-1 h-3.5 w-3.5" />Ask Pumpkin</Button>
			{/if}

			<Button aria-label="Copy" class="size-8 rounded-full" onclick={copy} size="icon" variant="ghost"><Copy class="h-4 w-4" /></Button>
			<Button aria-label="Download" class="size-8 rounded-full" onclick={download} size="icon" variant="ghost"><Download class="h-4 w-4" /></Button>
			<Button aria-label="Delete" class="size-8 rounded-full" onclick={remove} size="icon" variant="ghost"><Trash class="h-4 w-4" /></Button>
		</div>

		<div class={['min-h-0 flex-1 overflow-auto rounded-xl border border-border/60', tall ? 'min-h-[60vh]' : 'min-h-[22rem]']}>
			{#if editing}
				<textarea
					aria-label="Edit {art.title}"
					bind:value={draft}
					class="h-full min-h-[22rem] w-full resize-none bg-transparent p-3 font-mono text-xs outline-none"
					spellcheck="false"
				></textarea>
			{:else if view === 'preview' && art.doc}
				<iframe class="h-full min-h-[22rem] w-full bg-white" referrerpolicy="no-referrer" sandbox="allow-scripts" srcdoc={art.doc} title={art.title}></iframe>
			{:else if view === 'preview' && art.kind === 'md'}
				<div class="p-4"><MarkdownContent content={art.content} /></div>
			{:else}
				<pre class="p-3 text-xs break-words whitespace-pre-wrap">{art.content}</pre>
			{/if}
		</div>

		<p class="text-xs text-muted-foreground">
			{art.kind} · version {art.shown}{isOld ? ' (older)' : ''} · last changed by {art.by === 'you' ? 'you' : 'the model'}, {when(art.updated)}
			{#if art.doc}· runs in a frame with no internet and no access to Pumpkin{/if}
		</p>
	</div>
{/if}
