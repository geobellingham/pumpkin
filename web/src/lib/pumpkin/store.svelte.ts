/**
 * Pumpkin: talks to the Pumpkin server (/pumpkin/api) that serves this UI. When the page is served by
 * a plain llama-server the API is missing, `available` stays false, and every Pumpkin control hides.
 */
import { modelsStore } from '$lib/stores/models/index.svelte';
import { serverStore } from '$lib/stores/server.svelte';
import type * as T from './types';

const API = './pumpkin/api/';

export class ApiError extends Error {}

export async function api<R = unknown>(
	path: string,
	options: { body?: unknown; method?: string } = {}
): Promise<R> {
	const method = options.method ?? (options.body !== undefined ? 'POST' : 'GET');
	const r = await fetch(API + path, {
		body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
		credentials: 'same-origin',
		headers: method === 'GET' ? {} : { 'Content-Type': 'application/json', 'X-Pumpkin': '1' },
		method
	});

	if (r.status === 401) {
		location.href = './login';

		throw new ApiError('Signed out');
	}

	const j = await r.json().catch(() => ({}));

	if (!r.ok || (j && typeof j === 'object' && 'error' in j && j.error)) {
		const e = (j as { error?: unknown }).error;

		throw new ApiError(typeof e === 'string' ? e : `Request failed (${r.status})`);
	}

	return j as R;
}

class PumpkinStore {
	/** null until the first probe answers */
	available = $state<boolean | null>(null);
	state = $state<T.State | null>(null);
	offline = $state(false);
	/** Text a Pumpkin action wants placed in the chat box; ChatScreen consumes it. */
	pendingInput = $state<string | null>(null);
	toast = $state<{ kind: 'error' | 'ok'; text: string } | null>(null);
	/** set by pages that want faster polling (downloads, installs, runs) */
	fast = $state(0);

	private started = false;
	private lastModel: string | null = null;
	private wasReady = false;

	get status(): T.Status {
		const s = this.state;

		if (this.offline || !s) return 'offline';

		const st = s.server.state;

		if (st === 'ready' || st === 'external') return s.agent.busy ? 'busy' : 'ready';

		if (st === 'loading') return 'loading';

		if (st === 'error') return 'error';

		return 'off';
	}

	get modelName(): string | null {
		const s = this.state;

		if (!s) return null;

		if (s.server.preset) return s.server.preset.name;

		return s.server.state === 'external' ? 'Started outside Pumpkin' : null;
	}

	start(): void {
		if (this.started || typeof window === 'undefined') return;

		this.started = true;
		void this.loop();
	}

	private async loop(): Promise<void> {
		await this.refresh();

		if (this.available === false) return;

		const s = this.state;
		const busy =
			this.fast > 0 ||
			this.offline ||
			!!(s && (s.server.state === 'loading' || s.agent.busy || s.downloads > 0 || s.installs.length));

		setTimeout(() => void this.loop(), busy ? 1200 : 4000);
	}

	async refresh(): Promise<void> {
		try {
			const r = await fetch(API + 'state', { credentials: 'same-origin' });

			if (r.status === 401) {
				location.href = './login';

				return;
			}

			const ct = r.headers.get('content-type') ?? '';

			if (!r.ok || !ct.includes('json')) {
				if (this.available !== true) this.available = false;
				else this.offline = true;

				return;
			}

			const s = (await r.json()) as T.State;

			if (!s || !s.server) {
				if (this.available !== true) this.available = false;

				return;
			}

			this.available = true;
			this.offline = false;
			this.state = s;
			this.sync(s);
		} catch {
			if (this.available === null) this.available = false;
			else this.offline = true;
		}
	}

	/** Keep the chat UI in step when a model starts, stops or changes. */
	private sync(s: T.State): void {
		const ready = s.server.state === 'ready' || s.server.state === 'external';
		const model = ready ? (s.server.preset?.id ?? 'external') : null;

		if (ready && (!this.wasReady || model !== this.lastModel)) {
			void serverStore.fetch({ background: true });
			void modelsStore.fetch(true);
		}

		this.wasReady = ready;
		this.lastModel = model;
	}

	say(text: string, kind: 'error' | 'ok' = 'ok'): void {
		this.toast = { kind, text };

		const mine = this.toast;

		setTimeout(() => {
			if (this.toast === mine) this.toast = null;
		}, kind === 'error' ? 7000 : 3500);
	}

	/** Runs an API call, shows its error as a toast, and refreshes the state afterwards. */
	async run<R>(fn: () => Promise<R>, okText?: string): Promise<R | null> {
		try {
			const r = await fn();

			if (okText) this.say(okText);

			return r;
		} catch (e) {
			this.say(e instanceof Error ? e.message : String(e), 'error');

			return null;
		} finally {
			void this.refresh();
		}
	}

	startPreset(id: string) {
		return this.run(() => api('llama/start', { body: { preset: id } }));
	}

	stopModel() {
		return this.run(() => api('llama/stop', { body: {} }));
	}

	sendToChat(text: string): void {
		this.pendingInput = text;
	}
}

export const pumpkin = new PumpkinStore();
