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
		Users,
		Layers,
		House,
		Image as ImageIcon,
		MonitorSmartphone,
		MessageSquareText,
		Plug,
		Settings,
		Sparkles,
		SlidersHorizontal,
		Smartphone,
		SquareTerminal,
		Stethoscope,
		Telescope
	} from '@lucide/svelte';
	import PumpkinLogo from '../components/PumpkinLogo.svelte';
	import StatusDot from '../components/StatusDot.svelte';
	import ThemeToggle from '../components/ThemeToggle.svelte';
	import { api, pumpkin } from '../store.svelte';
	import type { LlamaInfo } from '../types';
	import { onMount } from 'svelte';
	import Agent from './Agent.svelte';
	import Research from './Research.svelte';
	import Artifacts from './Artifacts.svelte';
	import Browser from './Browser.svelte';
	import Code from './Code.svelte';
	import Connectors from './Connectors.svelte';
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
	import Skills from './Skills.svelte';
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
		{ icon: Telescope, id: 'research', label: 'Research', page: Research },
		{ icon: SquareTerminal, id: 'code', label: 'Code', page: Code },
		{ icon: Users, id: 'browser', label: 'Teammate', page: Browser },
		{ icon: Layers, id: 'artifacts', label: 'Artifacts', page: Artifacts },
		{ icon: Sparkles, id: 'skills', label: 'Skills', page: Skills },
		{ icon: Plug, id: 'connectors', label: 'Connectors', page: Connectors },
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

	// the line under each page's title. The Overview has its own greeting and Code fills the screen, so neither gets one
	const SUB: Record<string, string> = {
		models: 'Find, download and manage the models on this computer.',
		presets: 'A model plus how to run it: context, memory and tools.',
		agent: 'Personas, tools and limits for the model.',
		research: 'Ask a question and get a report with its sources.',
		browser: 'The model works in your browser, next to you.',
		artifacts: 'Pages, drawings and documents the model made.',
		skills: 'Instructions the model picks up when a task needs them.',
		connectors: 'Give the model your apps and services.',
		tasks: 'Jobs that run by themselves, now or on a schedule.',
		files: 'Your Files folder, shared with the model.',
		images: 'Draw, edit and animate pictures with local models.',
		memory: 'What the model remembers about you.',
		prompts: 'Prompts you use again and again.',
		reports: 'Speed tests and what your tasks did.',
		voice: 'Talk to the model and hear it answer.',
		devices: 'Your other computers running Pumpkin.',
		phone: 'Use Pumpkin from your phone.',
		doctor: 'Checks that everything works, and fixes for what does not.',
		settings: 'How Pumpkin runs on this computer.'
	};
	const Page = $derived(current?.page ?? Setup);

	let navEl: HTMLElement | undefined = $state();
	let pill = $state({ left: 0, top: 0, width: 0, height: 0, ready: false });

	// the highlight slides from the old item to the new one (down the list on a computer, along the line on a
	// phone). On a phone the menu is one scrolling line, so the active item also glides into the middle. Only the
	// menu scrolls sideways; the page itself never moves.
	function place(smooth: boolean) {
		const el = navEl?.querySelector<HTMLElement>('[aria-current="page"]');
		if (!navEl || !el) return;
		pill = { left: el.offsetLeft, top: el.offsetTop, width: el.offsetWidth, height: el.offsetHeight, ready: pill.ready || !smooth };
		if (navEl.scrollWidth > navEl.clientWidth) {
			const left = el.offsetLeft - (navEl.clientWidth - el.offsetWidth) / 2;
			navEl.scrollTo({ left: Math.max(0, left), behavior: smooth ? 'smooth' : 'auto' });
		}
		if (!pill.ready) requestAnimationFrame(() => (pill.ready = true));
	}

	// the llama.cpp build at the foot of the menu, like "b5821 · CUDA"
	let build = $state<string | null>(null);

	onMount(async () => {
		const r = await api<LlamaInfo>('llama').catch(() => null);
		const v = r?.info?.version;

		if (v) build = `${v.match(/b?\d{3,}/)?.[0]?.replace(/^(\d)/, 'b$1') ?? v} · ${(r?.backend ?? '').toUpperCase()}`.replace(/ · $/, '');
	});

	// a phone turned sideways (or a window resized past the phone width) moves the items
	function onResize() {
		place(false);
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

	/* the active item's glowing highlight. It moves with a little overshoot, like the mockup's spring */
	.nav-ind {
		position: absolute;
		left: 0;
		top: 0;
		border-radius: 999px;
		background: var(--pk-nav-ind);
		box-shadow: var(--pk-nav-ind-shadow);
		pointer-events: none;
	}

	.nav-ind.ready {
		transition:
			transform 0.75s cubic-bezier(0.34, 1.35, 0.64, 1),
			width 0.45s var(--pk-smooth),
			height 0.45s var(--pk-smooth);
	}

	@media (min-width: 768px) {
		.nav-ind {
			border-radius: 14px;
		}
	}

	.nav-item :global(svg) {
		transition: transform 0.5s var(--pk-spring);
	}

	@media (hover: hover) {
		.nav-item:hover :global(svg) {
			transform: scale(1.15) rotate(-6deg);
		}
	}
</style>

<svelte:window onresize={onResize} />

{#if pumpkin.available === false}
	<div class="mx-auto max-w-md px-6 pt-24 text-center text-sm text-muted-foreground">
		This page needs the Pumpkin server. Open Pumpkin with the <code>pumpkin</code> command.
	</div>
{:else}
	<div class={['pk-scope mx-auto w-full px-4 pt-3 pb-24 md:px-6 md:pt-5', section === 'code' ? 'max-w-[96rem]' : 'max-w-[1400px]']}>
		<header class="pk-glass pk-rise flex h-14 items-center gap-2 rounded-[22px] pr-3 pl-2 md:pr-4">
			<a
				aria-label="Back to chat"
				class="flex size-10 items-center justify-center rounded-full transition-[background-color,transform] duration-300 hover:bg-[var(--pk-soft)] active:scale-95"
				href="#/"
			>
				<ChevronLeft class="h-5 w-5" />
			</a>

			<PumpkinLogo class="size-7" />

			<h1 class="text-base font-bold tracking-[-0.01em]">Pumpkin</h1>

			{#if pumpkin.state?.version}
				<span class="pk-chip max-sm:hidden">v{pumpkin.state.version}</span>
			{/if}

			<span class="ml-auto"><ThemeToggle /></span>
			<span
				class={[
					'flex h-9 min-w-0 items-center gap-2 rounded-full px-3 text-xs',
					pumpkin.status === 'ready' || pumpkin.status === 'busy'
						? 'bg-[rgba(62,224,138,0.1)] text-[color:var(--pk-text)] shadow-[inset_0_1px_0_rgba(255,255,255,0.08)]'
						: 'text-muted-foreground'
				]}
			>
				<StatusDot />
				<span class={['truncate', pumpkin.modelName && 'font-mono']}>
					{#if (pumpkin.status === 'ready' || pumpkin.status === 'busy') && pumpkin.state?.server.port}
						<span class="max-sm:hidden">llama-server :{pumpkin.state.server.port}</span><span class="sm:hidden">{pumpkin.modelName}</span>
					{:else}
						{pumpkin.modelName ?? (pumpkin.status === 'offline' ? 'Offline' : 'No model running')}
					{/if}
				</span>
			</span>
		</header>

		{#if current}
			<div class="mt-3 md:mt-5 md:grid md:grid-cols-[210px_minmax(0,1fr)] md:gap-4">
				<nav
					aria-label="Pumpkin sections"
					bind:this={navEl}
					class="pumpkin-nav pk-glass pk-rise sticky top-0 z-10 -mx-4 flex gap-1 overflow-x-auto rounded-none px-4 py-2 md:relative md:mx-0 md:flex-col md:self-start md:overflow-visible md:rounded-[22px] md:p-2"
				>
					<span
						aria-hidden="true"
						class={['nav-ind', pill.ready && 'ready']}
						style="transform: translate({pill.left}px, {pill.top}px); width: {pill.width}px; height: {pill.height}px; opacity: {pill.width ? 1 : 0}"
					></span>
					{#each NAV.filter((n) => !(pumpkin.state?.platform === 'android' && (n.id === 'phone' || n.id === 'images' || n.id === 'connectors' || n.id === 'browser'))) as n (n.id)}
						{@const Icon = n.icon}
						<a
							aria-current={current.id === n.id ? 'page' : undefined}
							class={[
								'nav-item relative z-[1] flex h-9 shrink-0 items-center gap-3 rounded-full px-3.5 text-[13px] font-medium whitespace-nowrap transition-colors duration-300 md:h-[42px] md:rounded-[14px] md:px-3',
								current.id === n.id ? 'text-[color:var(--pk-nav-active)]' : 'text-muted-foreground hover:text-foreground'
							]}
							href="#/pumpkin/{n.id}"
						>
							<Icon class="size-[18px] max-md:hidden" strokeWidth={1.8} />
							{n.label}
						</a>
					{/each}
					{#if build}
						<div class="mt-3.5 px-3 pt-3.5 pb-1.5 shadow-[inset_0_1px_0_rgba(255,255,255,0.06)] max-md:hidden">
							<div class="text-[11px] text-muted-foreground">llama.cpp build</div>
							<div class="mt-0.5 font-mono text-xs text-[color:var(--pk-text)] opacity-90">{build}</div>
						</div>
					{/if}
				</nav>

				<main class="min-w-0 pt-3 md:pt-0">
					{#key current.id}
						<div in:fly={{ duration: 220, y: 10, easing: cubicOut }}>
							{#if current.id !== 'overview' && current.id !== 'code'}
								<div class="pk-rise mb-4 px-1.5 pt-1">
									<h1 class="text-[30px] leading-tight font-semibold tracking-[-0.025em]">{current.label}</h1>
									{#if SUB[current.id]}<p class="mt-1 text-[13px] text-muted-foreground">{SUB[current.id]}</p>{/if}
								</div>
							{/if}
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
