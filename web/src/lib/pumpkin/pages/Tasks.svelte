<script lang="ts">
	import { LoaderCircle, Pencil, Play, Plus, Trash } from '@lucide/svelte';
	import { Button } from '$lib/components/ui/button';
	import * as Dialog from '$lib/components/ui/dialog';
	import { Input } from '$lib/components/ui/input';
	import { Switch } from '$lib/components/ui/switch';
	import { Textarea } from '$lib/components/ui/textarea';
	import Card from '../components/Card.svelte';
	import Empty from '../components/Empty.svelte';
	import Field from '../components/Field.svelte';
	import Select from '../components/Select.svelte';
	import Toggle from '../components/Toggle.svelte';
	import { api, pumpkin } from '../store.svelte';
	import { TASK_TEMPLATES, type TaskTemplate } from '../templates';
	import type { Preset, Task } from '../types';
	import { onDestroy, onMount } from 'svelte';

	let tasks = $state<Task[] | null>(null);
	let presets = $state<Preset[]>([]);
	let editing = $state<Task | null>(null);
	let open = $state(false);
	let lines = $state<{ n: number; text: string }[]>([]);
	let job = $state<string | null>(null);
	let busy = $state(false);
	let logEl: HTMLPreElement | undefined = $state();
	let timer: ReturnType<typeof setInterval> | undefined;

	async function load() {
		const [t, p] = await Promise.all([
			pumpkin.run(() => api<{ items: Task[] }>('tasks')),
			pumpkin.run(() => api<{ items: Preset[] }>('presets'))
		]);

		tasks = t?.items ?? [];
		presets = p?.items ?? [];
	}

	async function poll() {
		const since = lines.length ? lines[lines.length - 1].n : 0;
		const r = await api<{ busy: boolean; job: string | null; lines: { n: number; text: string }[] }>(`agent?since=${since}`).catch(() => null);

		if (!r) return;

		if (r.lines.length && r.lines[0].n === 1 && since > 0) lines = [];

		lines = [...lines, ...r.lines].slice(-400);

		if (busy && !r.busy) void load();

		busy = r.busy;
		job = r.job;
	}

	onMount(() => {
		void load();
		void poll();
		timer = setInterval(poll, 1500);
	});

	onDestroy(() => clearInterval(timer));

	$effect(() => {
		void lines.length;

		if (logEl) logEl.scrollTop = logEl.scrollHeight;
	});

	function edit(t?: Task, tpl?: TaskTemplate) {
		editing = t
			? { ...t }
			: {
					days: tpl?.days ?? 'weekdays',
					enabled: !tpl?.prompt.includes('<'),
					id: '',
					name: tpl?.name ?? '',
					preset: presets[0]?.id ?? '',
					prompt: tpl?.prompt ?? '',
					stop_after: false,
					time: tpl?.time ?? '08:00'
				};
		open = true;
	}

	async function save() {
		if (!editing) return;

		const e = editing;
		const r = e.id
			? await pumpkin.run(() => api(`tasks/${e.id}`, { body: e, method: 'PUT' }))
			: await pumpkin.run(() => api('tasks', { body: e }));

		if (r) {
			open = false;
			void load();
		}
	}

	async function toggle(t: Task, enabled: boolean) {
		t.enabled = enabled;
		await pumpkin.run(() => api(`tasks/${t.id}`, { body: { enabled }, method: 'PUT' }));
	}

	async function run(t: Task) {
		lines = [];

		if (await pumpkin.run(() => api('tasks/run', { body: { id: t.id } }))) {
			busy = true;
			void poll();
		}
	}

	async function remove(t: Task) {
		if (!confirm(`Delete the task “${t.name}”?`)) return;

		await pumpkin.run(() => api(`tasks/${t.id}`, { method: 'DELETE' }));
		void load();
	}

	const DAY_OPTIONS = [
		{ label: 'Every day', value: 'daily' },
		{ label: 'Weekdays', value: 'weekdays' },
		{ label: 'Weekends', value: 'weekends' }
	];
	const dayLabel = (d: string) => DAY_OPTIONS.find((o) => o.value === d)?.label ?? d;
	const presetName = (id: string) => presets.find((p) => p.id === id || p.name === id)?.name ?? 'the running model';
</script>

<div class="flex flex-col gap-4">
	{#if busy || lines.length}
		<Card title={busy ? `Running: ${job ?? 'task'}` : 'Last run'}>
			{#snippet actions()}
				{#if busy}
					<Button class="rounded-full" onclick={() => api('agent/cancel', { body: {} })} size="sm" variant="outline">Cancel</Button>
				{:else}
					<Button class="rounded-full" href="#/pumpkin/reports" size="sm" variant="ghost">Reports</Button>
				{/if}
			{/snippet}

			<pre
				bind:this={logEl}
				class="max-h-72 overflow-auto rounded-xl bg-muted/60 p-3 font-mono text-xs leading-relaxed break-words whitespace-pre-wrap">{lines.map((l) => l.text).join('\n')}</pre>
		</Card>
	{/if}

	<Card description="Agent jobs that run on a schedule. Each one saves a report and can notify your phone." title="Tasks">
		{#snippet actions()}
			<Button class="rounded-full" onclick={() => edit()} size="sm"><Plus class="h-4 w-4" /> New</Button>
		{/snippet}

		{#if tasks === null}
			<div class="flex justify-center py-8"><LoaderCircle class="h-5 w-5 animate-spin text-muted-foreground" /></div>
		{:else if !tasks.length}
			<Empty text="No tasks yet. A task is a prompt that runs by itself, like a morning news briefing." />
		{:else}
			<div class="flex flex-col divide-y divide-border/60">
				{#each tasks as t (t.id)}
					<div class="flex flex-wrap items-center gap-x-3 gap-y-2 py-3">
						<div class="min-w-0 flex-1 basis-56">
							<div class="truncate font-medium">{t.name}</div>
							<div class="text-xs text-muted-foreground">
								{t.time} · {dayLabel(t.days)} · {presetName(t.preset)}
							</div>
							<div class={['text-xs', t.last_error ? 'text-destructive' : 'text-muted-foreground']}>
								{t.last_error ? `Last run failed: ${t.last_error}` : t.last_run ? `Last run ${t.last_run}` : 'Never run'}
							</div>
						</div>
						<Switch aria-label="Schedule {t.name}" checked={t.enabled} onCheckedChange={(v) => toggle(t, v)} />
						<div class="flex gap-1">
							<Button class="rounded-full" disabled={busy} onclick={() => run(t)} size="sm" variant="secondary"><Play class="h-3.5 w-3.5" /> Run</Button>
							<Button aria-label="Edit {t.name}" class="size-9 rounded-full" onclick={() => edit(t)} size="icon" variant="ghost"><Pencil class="h-4 w-4" /></Button>
							<Button aria-label="Delete {t.name}" class="size-9 rounded-full" onclick={() => remove(t)} size="icon" variant="ghost"><Trash class="h-4 w-4" /></Button>
						</div>
					</div>
				{/each}
			</div>
			<p class="mt-3 text-xs text-muted-foreground">
				Schedules run while Pumpkin is running. Install the login service (Settings) so they run with the window closed.
			</p>
		{/if}
	</Card>

	<Card description="Pick one, change what you like, and save. Words in angle brackets need your own details." title="Start from a template">
		<div class="grid gap-3 sm:grid-cols-2">
			{#each TASK_TEMPLATES as tpl (tpl.name)}
				<button
					class="flex flex-col gap-0.5 rounded-xl border border-border/60 p-3 text-left transition-colors hover:bg-muted/50 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
					onclick={() => edit(undefined, tpl)}
					type="button"
				>
					<span class="text-sm font-medium">{tpl.name}</span>
					<span class="text-xs text-muted-foreground">{tpl.summary}</span>
				</button>
			{/each}
		</div>
	</Card>
</div>

<Dialog.Root bind:open>
	<Dialog.Content class="flex max-h-[100dvh] flex-col gap-0 p-0 max-md:h-[100dvh]! max-md:w-screen! max-md:max-w-none! max-md:rounded-none! md:w-[34rem]! md:max-w-[calc(100vw-2rem)]!">
		{#if editing}
			<div class="px-5 pt-[max(1.25rem,env(safe-area-inset-top))] pb-2">
				<Dialog.Title class="text-base font-semibold">{editing.id ? 'Edit task' : 'New task'}</Dialog.Title>
			</div>
			<div class="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-5 py-3">
				<Field label="Name"><Input bind:value={editing.name} placeholder="e.g. Morning briefing" /></Field>
				<Field hint="Written like a chat message. The agent can search the web and use its tools." label="What to do">
					<Textarea bind:value={editing.prompt} class="min-h-32" />
				</Field>
				<div class="grid grid-cols-2 gap-3">
					<Field label="Time"><Input bind:value={editing.time} type="time" /></Field>
					<Field label="Days"><Select bind:value={editing.days} options={DAY_OPTIONS} /></Field>
				</div>
				<Field hint="Pumpkin starts this preset if a different model is running." label="Preset">
					<Select bind:value={editing.preset} options={[{ label: 'Whatever is running', value: '' }, ...presets.map((p) => ({ label: p.name, value: p.id }))]} />
				</Field>
				<Toggle bind:checked={editing.stop_after as boolean} description="Frees the GPU when the task is done, if Pumpkin started the model for it." label="Stop the model afterwards" />
				<Toggle bind:checked={editing.enabled} label="Scheduled" />
			</div>
			<div class="flex justify-end gap-2 border-t border-border/60 px-5 py-3 pb-[max(0.75rem,env(safe-area-inset-bottom))]">
				<Button class="rounded-full" onclick={() => (open = false)} variant="ghost">Cancel</Button>
				<Button class="rounded-full" disabled={!editing.name.trim() || !editing.prompt.trim()} onclick={save}>Save task</Button>
			</div>
		{/if}
	</Dialog.Content>
</Dialog.Root>
