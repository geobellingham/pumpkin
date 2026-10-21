<script lang="ts">
	let { class: className = 'size-5' }: { class?: string } = $props();
</script>

<svg aria-hidden="true" class={className} viewBox="0 0 32 32">
	<path d="M16.6 8.2c.2-2.4 1.3-4.3 3.4-5.6" fill="none" stroke="#4d7c0f" stroke-linecap="round" stroke-width="2.4" />
	<ellipse cx="10.4" cy="18.6" fill="#ea580c" rx="7.4" ry="9.4" />
	<ellipse cx="21.6" cy="18.6" fill="#ea580c" rx="7.4" ry="9.4" />
	<ellipse cx="16" cy="18.6" fill="#f97316" rx="7" ry="10" />
	<path
		d="M16 9.2v18.8M10.8 10.6c-1.6 2.4-2.4 5.1-2.4 8s.8 5.6 2.4 8M21.2 10.6c1.6 2.4 2.4 5.1 2.4 8s-.8 5.6-2.4 8"
		fill="none"
		stroke="#c2410c"
		stroke-linecap="round"
		stroke-opacity=".55"
		stroke-width="1.1"
	/>
</svg>
