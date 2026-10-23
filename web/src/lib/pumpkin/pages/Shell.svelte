<script lang="ts">
	import {
		AudioLines,
		Bot,
		Boxes,
		Brain,
		ChevronLeft,
		Clock,
		FileText,
		FolderOpen,
		House,
		Image as ImageIcon,
		MonitorSmartphone,
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
	import Devices from './Devices.svelte';
	import Doctor from './Doctor.svelte';
	import Files from './Files.svelte';
	import Images from './Images.svelte';
	import Memory from './Memory.svelte';
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
	import { cubicOut } from 'svelte/easing';
	import { fly } from 'svelte/transition';

	let { section }: { section: string } = $props();

	const NAV: { icon: Component; id: string; label: string; page: Component }[] = [
		{ icon: House, id: 'overview', label: 'Overview', page: Overview },
		{ icon: Boxes, id: 'models', label: 'Models', page: Models },
		{ icon: SlidersHorizontal, id: 'presets', label: 'Presets', page: Presets },
		{ icon: Bot, id: 'agent', label: 'Agent', page: Agent },
		{ icon: Clock, id: 'tasks', label: 'Tasks', page: Tasks },
		{ icon: FolderOpen, id: 'files', label: 'Files', page: Files },
		{ icon: ImageIcon, id: 'images', label: 'Images', page: Images },
		{ icon: Brain, id: 'memory', label: 'Memory', page: Memory },
		{ icon: MessageSquareText, id: 'prompts', label: 'Prompts', page: Prompts },
		{ icon: FileText, id: 'reports', label: 'Reports', page: Reports },
		{ icon: AudioLines, id: 'voice', label: 'Voice', page: Voice },
		{ icon: MonitorSmartphone, id: 'devices', label: 'Devices', page: Devices },
		{ icon: Smartphone, id: 'phone', label: 'Phone', page: Phone },
		{ icon: Stethoscope, id: 'doctor', label: 'Doctor', page: Doctor },
		{ icon: Settings, id: 'settings', label: 'Settings', page: SettingsPage }
	];

	const current = $derived(section === 'setup' ? null : (NAV.find((n) => n.id === section) ?? NAV[0]));
	const Page = $derived(current?.page ?? Setup);

	let navEl: HTMLElement | undefined = $state();
	let pill = $state({ left: 0, width: 0, ready: false });

	// phones: the menu is one scrolling line. Glide the active pill into the middle and slide the highlight
	// under it. Only the menu scrolls sideways; the page itself never moves.
	function place(smooth: boolean) {
		const el = navEl?.querySelector<HTMLElement>('[aria-current="page"]');
		if (!navEl || !el) return;
		pill = { left: el.offsetLeft, width: el.offsetWidth, ready: pill.ready || !smooth };
		const left = el.offsetLeft - (navEl.clientWidth - el.offsetWidth) / 2;
		navEl.scrollTo({ left: Math.max(0, left), behavior: smooth ? 'smooth' : 'auto' });
		if (!pill.ready) requestAnimationFrame(() => (pill.ready = true));
	}

	let first = true;
	$effect(() => {
		void section;
		void navEl;
		requestAnimationFrame(() => {
			place(!first);
			first = false;
		});
	});

	// a new section starts at its top, keeping the menu in view
	let lastSection = section;
	$effect(() => {
		if (section === lastSection) return;
		lastSection = section;
		const top = navEl ? navEl.getBoundingClientRect().top + window.scrollY - 8 : 0;
		if (window.scrollY > top) window.scrollTo({ top, behavior: 'auto' });
	});

</script>

<style>
	/* the menu line fades out at the edges so a cut-off word reads as "scroll for more" */
	.pumpkin-nav {
		scrollbar-width: none;
		-webkit-mask-image: linear-gradient(to right, transparent, #000 1.25rem, #000 calc(100% - 1.25rem), transparent);
		mask-image: linear-gradient(to right, transparent, #000 1.25rem, #000 calc(100% - 1.25rem), transparent);
	}

	.pumpkin-nav::-webkit-scrollbar {
		display: none;
	}

	@media (min-width: 768px) {
		.pumpkin-nav {
			-webkit-mask-image: none;
			mask-image: none;
		}
	}
</style>

{#if pumpkin.available === false}
	<div class="mx-auto max-w-md px-6 pt-24 text-center text-sm text-muted-foreground">
		This page needs the Pumpkin server. Open Pumpkin with the <code>pumpkin</code> command.
	</div>
{:else}
	<div class="mx-auto w-full max-w-5xl px-5 pt-3 pb-24 md:px-8 md:pt-6">
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
					class="pumpkin-nav sticky top-0 z-10 -mx-5 flex gap-1 overflow-x-auto bg-background px-5 py-2 md:static md:mx-0 md:flex-col md:self-start md:overflow-visible md:bg-transparent md:p-0"
				>
					<span
						aria-hidden="true"
						class={['pointer-events-none absolute top-2 h-9 rounded-full bg-foreground md:hidden', pill.ready && 'transition-[left,width] duration-300 ease-out']}
						style="left: {pill.left}px; width: {pill.width}px; opacity: {pill.width ? 1 : 0}"
					></span>
					{#each NAV.filter((n) => !(pumpkin.state?.platform === 'android' && (n.id === 'phone' || n.id === 'images'))) as n (n.id)}
						{@const Icon = n.icon}
						<a
							aria-current={current.id === n.id ? 'page' : undefined}
							class={[
								'relative flex h-9 shrink-0 items-center gap-2 rounded-full px-3.5 text-sm whitespace-nowrap transition-colors duration-300 md:rounded-lg md:px-3',
								current.id === n.id
									? 'text-background md:bg-muted md:text-foreground md:font-medium'
									: 'text-muted-foreground md:hover:bg-muted hover:text-foreground'
							]}
							href="#/pumpkin/{n.id}"
						>
							<Icon class="h-4 w-4 max-md:hidden" />
							{n.label}
						</a>
					{/each}
				</nav>

				<main class="min-w-0 pt-3 md:pt-0">
					{#key current.id}
						<div in:fly={{ duration: 220, y: 10, easing: cubicOut }}>
							<Page />
						</div>
					{/key}
				</main>
			</div>
		{:else}
			<main class="mx-auto mt-6 max-w-2xl"><Setup /></main>
		{/if}
	</div>
{/if}
