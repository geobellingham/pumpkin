<script lang="ts">
	import { ChevronRight, Download, Eye, File, FileText, Folder, FolderOpen, Image, LoaderCircle, Trash, Upload } from '@lucide/svelte';
	import { Button } from '$lib/components/ui/button';
	import * as Dialog from '$lib/components/ui/dialog';
	import Card from '../components/Card.svelte';
	import Empty from '../components/Empty.svelte';
	import { api, pumpkin } from '../store.svelte';
	import { onDestroy, onMount } from 'svelte';

	interface Item {
		dir: boolean;
		mtime: number;
		name: string;
		path: string;
		size: number;
		type: string;
	}

	interface Listing {
		display: string;
		items: Item[];
		path: string;
		sandbox: string;
	}

	let dir = $state('');
	let data = $state<Listing | null>(null);
	let uploading = $state(false);
	let picker: HTMLInputElement | undefined = $state();
	let preview = $state<{ item: Item; text?: string } | null>(null);
	let previewOpen = $state(false);
	let timer: ReturnType<typeof setInterval> | undefined;

	// the folder can be opened in a file manager only on the computer itself (not from a phone, not in the Android app)
	const local = ['127.0.0.1', 'localhost', '[::1]'].includes(location.hostname) && pumpkin.state?.platform !== 'android';
	const href = (p: string, inline = false) => `./pumpkin/api/files/download?path=${encodeURIComponent(p)}${inline ? '&inline=1' : ''}`;

	async function load(path = dir) {
		const r = await api<Listing>(`files?path=${encodeURIComponent(path)}`).catch((e) => {
			pumpkin.say(e.message, 'error');

			return null;
		});

		if (r) {
			data = r;
			dir = r.path;
		}
	}

	onMount(() => {
		void load('');
		timer = setInterval(() => load(), 4000);       // new files appear while the AI works
	});

	onDestroy(() => clearInterval(timer));

	async function upload(files: FileList | null) {
		if (!files?.length) return;

		uploading = true;

		for (const f of Array.from(files)) {
			const r = await fetch(`./pumpkin/api/files/upload?dir=${encodeURIComponent(dir)}&name=${encodeURIComponent(f.name)}`, {
				body: f,
				credentials: 'same-origin',
				headers: { 'Content-Type': 'application/octet-stream', 'X-Pumpkin': '1' },
				method: 'POST'
			});

			if (!r.ok) {
				const j = await r.json().catch(() => ({}));

				pumpkin.say((j as { error?: string }).error ?? `Could not add ${f.name}`, 'error');
			}
		}

		uploading = false;
		pumpkin.say(files.length === 1 ? `Added ${files[0].name}` : `Added ${files.length} files`);
		if (picker) picker.value = '';
		void load();
	}

	async function remove(it: Item) {
		if (!confirm(`Delete ${it.name}${it.dir ? ' and everything in it' : ''}?`)) return;

		await pumpkin.run(() => api('files/delete', { body: { path: it.path } }), 'Deleted');
		void load();
	}

	const isText = (it: Item) => it.type.startsWith('text/') || /\.(md|txt|py|json|csv|log|ya?ml|sh|js|ts|html?|css)$/i.test(it.name);
	const isImage = (it: Item) => /^image\/(png|jpeg|gif|webp)$/.test(it.type);
	const canView = (it: Item) => isText(it) || isImage(it) || it.type === 'application/pdf';

	async function view(it: Item) {
		preview = { item: it };
		previewOpen = true;

		if (isText(it) && it.type !== 'application/pdf') {
			const r = await fetch(href(it.path, true), { credentials: 'same-origin' });
			const text = await r.text();

			preview = { item: it, text: text.length > 200_000 ? text.slice(0, 200_000) + '\n…' : text };
		}
	}

	const size = (n: number) => (n >= 2 ** 30 ? `${(n / 2 ** 30).toFixed(1)} GB` : n >= 2 ** 20 ? `${(n / 2 ** 20).toFixed(1)} MB` : n >= 1024 ? `${Math.round(n / 1024)} KB` : `${n} B`);

	function ago(t: number) {
		const s = Date.now() / 1000 - t;

		return s < 60 ? 'just now' : s < 3600 ? `${Math.round(s / 60)} min ago` : s < 86400 ? `${Math.round(s / 3600)} h ago` : new Date(t * 1000).toLocaleDateString();
	}

	const crumbs = $derived(dir ? dir.split('/').map((name, i, all) => ({ name, path: all.slice(0, i + 1).join('/') })) : []);
</script>

<div class="flex flex-col gap-4">
	<Card description={data ? `What the AI makes is saved here. On this computer: ${data.display}. The AI sees it as ${data.sandbox}.` : ''} title="Files">
		{#snippet actions()}
			{#if local}
				<Button class="rounded-full" onclick={() => api('files/reveal', { body: { path: dir } }).catch((e) => pumpkin.say(e.message, 'error'))} size="sm" variant="ghost">
					<FolderOpen class="h-4 w-4" /> Open folder
				</Button>
			{/if}
			<Button class="rounded-full" disabled={uploading} onclick={() => picker?.click()} size="sm">
				{#if uploading}<LoaderCircle class="h-4 w-4 animate-spin" />{:else}<Upload class="h-4 w-4" />{/if} Add files
			</Button>
			<input bind:this={picker} class="hidden" multiple onchange={(e) => upload((e.currentTarget as HTMLInputElement).files)} type="file" />
		{/snippet}

		<nav aria-label="Folder" class="mb-2 flex flex-wrap items-center gap-1 text-sm">
			<button class={['rounded-md px-1.5 py-0.5 hover:bg-muted', !dir && 'font-medium']} onclick={() => load('')} type="button">Files</button>
			{#each crumbs as c (c.path)}
				<ChevronRight class="h-3.5 w-3.5 text-muted-foreground" />
				<button class={['rounded-md px-1.5 py-0.5 hover:bg-muted', c.path === dir && 'font-medium']} onclick={() => load(c.path)} type="button">{c.name}</button>
			{/each}
		</nav>

		{#if data === null}
			<div class="flex justify-center py-8"><LoaderCircle class="h-5 w-5 animate-spin text-muted-foreground" /></div>
		{:else if !data.items.length}
			<Empty
				text={dir
					? 'This folder is empty.'
					: 'Nothing yet. Ask the AI to save something, for example: “Write a packing list for a weekend trip and save it as packing.md”. You can also add your own files for it to work on.'}
			/>
		{:else}
			<div class="flex flex-col divide-y divide-border/60">
				{#each data.items as it (it.path)}
					<div class="flex items-center gap-3 py-2">
						{#if it.dir}
							<button class="flex min-w-0 flex-1 items-center gap-3 rounded-lg py-1 text-left hover:bg-muted/40" onclick={() => load(it.path)} type="button">
								<Folder class="h-4 w-4 shrink-0 text-orange-600" />
								<span class="truncate text-sm font-medium">{it.name}</span>
								<span class="ml-auto shrink-0 pr-2 text-xs text-muted-foreground">{ago(it.mtime)}</span>
							</button>
						{:else}
							<button class="flex min-w-0 flex-1 items-center gap-3 rounded-lg py-1 text-left hover:bg-muted/40 disabled:cursor-default" disabled={!canView(it)} onclick={() => view(it)} type="button">
								{#if isImage(it)}<Image class="h-4 w-4 shrink-0 text-muted-foreground" />{:else if isText(it)}<FileText class="h-4 w-4 shrink-0 text-muted-foreground" />{:else}<File class="h-4 w-4 shrink-0 text-muted-foreground" />{/if}
								<span class="min-w-0 flex-1">
									<span class="block truncate text-sm">{it.name}</span>
									<span class="block text-xs text-muted-foreground">{size(it.size)} · {ago(it.mtime)}</span>
								</span>
							</button>
							{#if canView(it)}
								<Button aria-label="View {it.name}" class="size-9 rounded-full max-sm:hidden" onclick={() => view(it)} size="icon" variant="ghost"><Eye class="h-4 w-4" /></Button>
							{/if}
							<Button aria-label="Download {it.name}" class="size-9 rounded-full" download={it.name} href={href(it.path)} size="icon" variant="ghost"><Download class="h-4 w-4" /></Button>
						{/if}
						<Button aria-label="Delete {it.name}" class="size-9 rounded-full" onclick={() => remove(it)} size="icon" variant="ghost"><Trash class="h-4 w-4" /></Button>
					</div>
				{/each}
			</div>
		{/if}
	</Card>
</div>

<Dialog.Root bind:open={previewOpen}>
	<Dialog.Content class="flex max-h-[90dvh] flex-col gap-3 md:w-[52rem]! md:max-w-[calc(100vw-2rem)]!">
		{#if preview}
			<div class="flex items-center justify-between gap-3 pr-8">
				<Dialog.Title class="truncate text-base font-semibold">{preview.item.name}</Dialog.Title>
				<Button class="shrink-0 rounded-full" download={preview.item.name} href={href(preview.item.path)} size="sm" variant="secondary"><Download class="h-4 w-4" /> Download</Button>
			</div>
			<div class="min-h-0 flex-1 overflow-auto">
				{#if preview.item.type === 'application/pdf'}
					<iframe class="h-[70dvh] w-full rounded-lg border-0" src={href(preview.item.path, true)} title={preview.item.name}></iframe>
				{:else if isImage(preview.item)}
					<img alt={preview.item.name} class="mx-auto max-h-[70dvh] rounded-lg" src={href(preview.item.path, true)} />
				{:else if preview.text === undefined}
					<div class="flex justify-center py-8"><LoaderCircle class="h-5 w-5 animate-spin text-muted-foreground" /></div>
				{:else}
					<pre class="rounded-xl bg-muted/60 p-3 font-mono text-xs leading-relaxed break-words whitespace-pre-wrap">{preview.text}</pre>
				{/if}
			</div>
		{/if}
	</Dialog.Content>
</Dialog.Root>
