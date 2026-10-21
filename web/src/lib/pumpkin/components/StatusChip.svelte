<script lang="ts">
	import PumpkinLogo from './PumpkinLogo.svelte';
	import StatusDot from './StatusDot.svelte';
	import { pumpkin } from '../store.svelte';
	import { useSpeed } from '../speed.svelte';

	const speed = useSpeed();

	const label = $derived.by(() => {
		const inst = pumpkin.state?.installs.find((i) => i.state === 'running');

		if (inst) return inst.total ? `${Math.round((inst.done / inst.total) * 100)}%` : 'Installing';

		switch (pumpkin.status) {
			case 'offline':
				return 'Offline';
			case 'off':
				return 'No model';
			case 'error':
				return 'Model failed';
			case 'loading':
				return 'Loading';
			default:
				return speed.value != null ? `${Math.round(speed.value)} t/s` : 'Ready';
		}
	});
</script>

<a
	aria-label="Open Pumpkin, {label}"
	class="fixed top-4 right-3 z-20 flex h-10 items-center gap-2 rounded-full bg-muted/60 pr-3.5 pl-2.5 text-sm font-medium tabular-nums shadow-sm backdrop-blur-xl transition-colors hover:bg-muted md:right-4 md:h-9"
	href="#/pumpkin"
>
	<PumpkinLogo class="size-5" />

	<StatusDot />

	<span>{label}</span>
</a>
