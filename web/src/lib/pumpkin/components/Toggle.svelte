<script lang="ts">
	import { Switch } from '$lib/components/ui/switch';

	interface Props {
		checked: boolean;
		description?: string;
		disabled?: boolean;
		label: string;
		onchange?: (v: boolean) => void;
	}

	let { checked = $bindable(), description, disabled = false, label, onchange }: Props = $props();
</script>

<div class="flex items-center justify-between gap-4 py-1">
	<div class="min-w-0">
		<div class="text-sm font-medium">{label}</div>
		{#if description}<div class="text-xs leading-snug text-muted-foreground">{description}</div>{/if}
	</div>

	<Switch aria-label={label} bind:checked {disabled} onCheckedChange={(v) => onchange?.(v)} />
</div>
