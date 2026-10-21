import { chatStore } from '$lib/stores/chat/index.svelte';
import { conversationsStore } from '$lib/stores/conversations/index.svelte';
import { pumpkin } from './store.svelte';

const KEY = 'pumpkin.lastSpeed';

let remembered = $state<number | null>(null);

try {
	const v = Number(localStorage.getItem(KEY));

	if (v > 0) remembered = v;
} catch {
	/* storage can be blocked */
}

function remember(v: number): void {
	if (!(v > 0) || Math.abs((remembered ?? 0) - v) < 0.05) return;

	remembered = v;

	try {
		localStorage.setItem(KEY, v.toFixed(2));
	} catch {
		/* ignore */
	}
}

/**
 * Generation speed: live while a reply streams, otherwise the last finished reply's speed.
 * (llama-server's own metric drops to zero as soon as the model is idle.)
 */
export function useSpeed() {
	const live = $derived.by(() => {
		const s = chatStore.processing.activeState;

		if (!s || s.status !== 'generating') return null;

		return s.tokensPerSecond && s.tokensPerSecond > 0 ? s.tokensPerSecond : null;
	});

	const last = $derived.by(() => {
		const msgs = conversationsStore.activeMessages;

		for (let i = msgs.length - 1; i >= 0; i--) {
			const t = msgs[i].timings;

			if (!t) continue;

			const llm = t.agentic?.llm;
			const n = llm?.predicted_n ?? t.predicted_n;
			const ms = llm?.predicted_ms ?? t.predicted_ms;

			if (n && ms) return (n / ms) * 1000;
		}

		return null;
	});

	$effect(() => {
		const v = live ?? last;

		if (v) remember(v);
	});

	return {
		get isLive() {
			return live != null;
		},
		get value(): number | null {
			const st = pumpkin.status;

			if (st !== 'ready' && st !== 'busy') return null;

			const m = pumpkin.state?.metrics.predicted_tokens_seconds;

			return live ?? last ?? remembered ?? (m && m > 0 ? m : null);
		}
	};
}
