<script lang="ts">
	import {
		AudioLines,
		Bot,
		Boxes,
		ChevronLeft,
		Clock,
		FileText,
		FolderOpen,
		House,
		MessageSquareText,
		Settings,
		SlidersHorizontal,
		Smartphone,
		Stethoscope
	} from '@lucide/svelte';
	import PumpkinLogo from '../components/PumpkinLogo.svelte';
	import StatusDot from '../components/StatusDot.svelte';
	import { pumpkin } from '../store.svelte';
	import Agent from './Agent.svelte';
	import Doctor from './Doctor.svelte';
	import Files from './Files.svelte';
	import Models from './Models.svelte';
	import Overview from './Overview.svelte';
	import Phone from './Phone.svelte';
	import Presets from './Presets.svelte';
	import Prompts from './Prompts.svelte';
	import Reports from './Reports.svelte';
	import SettingsPage from './Settings.svelte';
	import Setup from './Setup.svelte';
	import Tasks from './Tasks.svelte';
	import Voice from './Voice.svelte';
	import type { Component } from 'svelte';

	let { section }: { section: string } = $props();

	const NAV: { icon: Component; id: string; label: string; page: Component }[] = [
		{ icon: House, id: 'overview', label: 'Overview', page: Overview },
		{ icon: Boxes, id: 'models', label: 'Models', page: Models },
		{ icon: SlidersHorizontal, id: 'presets', label: 'Presets', page: Presets },
		{ icon: Bot, id: 'agent', label: 'Agent', page: Agent },
		{ icon: Clock, id: 'tasks', label: 'Tasks', page: Tasks },
		{ icon: FolderOpen, id: 'files', label: 'Files', page: Files },
		{ icon: MessageSquareText, id: 'prompts', label: 'Prompts', page: Prompts },
		{ icon: FileText, id: 'reports', label: 'Reports', page: Reports },
		{ icon: AudioLines, id: 'voice', label: 'Voice', page: Voice },
		{ icon: Smartphone, id: 'phone', label: 'Phone', page: Phone },
		{ icon: Stethoscope, id: 'doctor', label: 'Doctor', page: Doctor },
		{ icon: Settings, id: 'settings', label: 'Settings', page: SettingsPage }
	];

	const current = $derived(section === 'setup' ? null : (NAV.find((n) => n.id === section) ?? NAV[0]));
	const Page = $derived(current?.page ?? Setup);

	let navEl: HTMLElement | undefined = $state();

	$effect(() => {
		// keep the active pill visible on phones
		void section;
		navEl?.querySelector('[aria-current="page"]')?.scrollIntoView({ block: 'nearest', inline: 'center' });
	});
</script>

{#if pumpkin.available === false}
	<div class="mx-auto max-w-md px-6 pt-24 text-center text-sm text-muted-foreground">
		This page needs the Pumpkin server. Open Pumpkin with the <code>pumpkin</code> command.
	</div>
{:else}
	<div class="mx-auto w-full max-w-5xl px-4 pt-3 pb-24 md:px-8 md:pt-6">
		<header class="flex h-12 items-center gap-2 pl-12 md:pl-0">
			<a
				aria-label="Back to chat"
				class="-ml-2 flex size-10 items-center justify-center rounded-full hover:bg-muted"
				href="#/"
			>
				<ChevronLeft class="h-5 w-5" />
			</a>

			<PumpkinLogo class="size-6" />

			<h1 class="text-lg font-semibold">Pumpkin</h1>

			<span class="ml-auto flex min-w-0 items-center gap-1.5 text-xs text-muted-foreground">
				<StatusDot />
				<span class="truncate">{pumpkin.modelName ?? (pumpkin.status === 'offline' ? 'Offline' : 'No model running')}</span>
			</span>
		</header>

		{#if current}
			<div class="mt-3 md:mt-6 md:grid md:grid-cols-[12rem_minmax(0,1fr)] md:gap-8">
				<nav
					aria-label="Pumpkin sections"
					bind:this={navEl}
					class="sticky top-0 z-10 -mx-4 flex gap-1 overflow-x-auto bg-background/90 px-4 py-2 backdrop-blur-xl md:static md:mx-0 md:flex-col md:self-start md:bg-transparent md:p-0 md:backdrop-blur-none"
					style="scrollbar-width: none"
				>
					{#each NAV as n (n.id)}
						{@const Icon = n.icon}
						<a
							aria-current={current.id === n.id ? 'page' : undefined}
							class={[
								'flex h-9 shrink-0 items-center gap-2 rounded-full px-3.5 text-sm whitespace-nowrap transition-colors md:rounded-lg md:px-3',
								current.id === n.id
									? 'bg-foreground text-background md:bg-muted md:text-foreground md:font-medium'
									: 'text-muted-foreground hover:bg-muted hover:text-foreground'
							]}
							href="#/pumpkin/{n.id}"
						>
							<Icon class="h-4 w-4 max-md:hidden" />
							{n.label}
						</a>
					{/each}
				</nav>

				<main class="min-w-0 pt-3 md:pt-0">
					<Page />
				</main>
			</div>
		{:else}
			<main class="mx-auto mt-6 max-w-2xl"><Setup /></main>
		{/if}
	</div>
{/if}
