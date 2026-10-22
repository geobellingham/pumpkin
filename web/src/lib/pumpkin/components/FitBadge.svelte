<script lang="ts">
	import type { Fit } from '../types';

	let { fit, short = false }: { fit?: Fit | null; short?: boolean } = $props();

	const SHORT: Record<string, string> = {
		gpu: 'Fits GPU',
		moe: 'RAM offload, fast',
		split: 'Slow, uses RAM',
		tight: 'Barely fits',
		no: 'Too big'
	};

	// no graphics card for AI: the processor does the work
	const CPU: Record<string, string> = { moe: 'Works, a bit slow', split: 'Works, slowly', tight: 'Barely fits' };
</script>

{#if fit}
	<span
		class={[
			'inline-flex shrink-0 items-center rounded-full px-2 py-0.5 text-[11px] font-medium',
			fit.level === 'gpu' && 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-400',
			fit.level === 'moe' && 'bg-sky-500/15 text-sky-700 dark:text-sky-400',
			(fit.level === 'split' || fit.level === 'tight') && 'bg-amber-500/15 text-amber-700 dark:text-amber-400',
			fit.level === 'no' && 'bg-red-500/15 text-red-700 dark:text-red-400'
		]}
		title="{fit.text} · needs about {fit.need_gb} GB"
	>
		{short ? (fit.phone && fit.level === 'gpu' ? 'Fits' : fit.cpu && CPU[fit.level] ? CPU[fit.level] : SHORT[fit.level]) : fit.text}
	</span>
{/if}
