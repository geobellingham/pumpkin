<script lang="ts">
	import { AudioLines } from '@lucide/svelte';
	import { Button } from '$lib/components/ui/button';
	import Card from '../components/Card.svelte';
	import Field from '../components/Field.svelte';
	import Select from '../components/Select.svelte';
	import Toggle from '../components/Toggle.svelte';
	import { pumpkinVoice as voice, VOICE_LANGUAGES } from '../voice.svelte';

	function start() {
		location.hash = '#/';
		setTimeout(() => voice.begin(), 300);
	}
</script>

<div class="flex flex-col gap-4">
	<Card>
		<div class="flex flex-col items-center gap-3 py-4 text-center">
			<button
				aria-label="Start voice chat"
				class="flex size-20 items-center justify-center rounded-full bg-muted shadow-sm transition-colors hover:bg-muted/70 disabled:opacity-50"
				disabled={!voice.supported}
				onclick={start}
				type="button"
			>
				<AudioLines class="size-8" />
			</button>
			<p class="max-w-sm text-sm text-muted-foreground">
				{#if voice.supported}
					Talk with the model hands-free: it listens, answers out loud sentence by sentence, then listens again. The
					same button sits next to the message box.
				{:else}
					This browser has no speech recognition. Use Safari on iPhone, or Chrome or Edge on Android and desktop.
				{/if}
			</p>
		</div>
	</Card>

	<Card title="Voice settings">
		<div class="flex flex-col gap-4">
			<Field label="Language">
				<Select onchange={(v) => voice.setLang(v)} options={VOICE_LANGUAGES.map((l) => ({ label: l.label, value: l.code }))} value={voice.lang} />
			</Field>
			<Toggle checked={voice.readTyped} description="Also speak answers to messages you type." label="Read typed replies aloud" onchange={(v) => voice.setReadTyped(v)} />
			<div><Button class="rounded-full" disabled={!voice.canSpeak} onclick={() => voice.testVoice()} size="sm" variant="secondary">Test the voice</Button></div>
			<p class="text-xs text-muted-foreground">
				Listening and speaking use this device, so nothing is sent anywhere else. For a natural voice, download an
				Enhanced or Premium voice in your system's speech settings (on iPhone: Accessibility > Spoken Content > Voices).
			</p>
		</div>
	</Card>
</div>
