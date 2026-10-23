/**
 * Pumpkin runs on your own computer, so an offline copy of the app helps nothing: with the server down nothing works
 * anyway. A service worker only gets in the way: after an update it can keep serving the old app, or hold the window
 * on an empty page. This takes the chat UI's place for registering one. It never registers a worker, and removes any
 * worker and caches an earlier version left behind.
 */
import { writable } from 'svelte/store';

export function useRegisterSW(_options?: unknown) {
	try {
		if (typeof navigator !== 'undefined' && navigator.serviceWorker) {
			void navigator.serviceWorker
				.getRegistrations()
				.then((all) => all.forEach((r) => void r.unregister()))
				.catch(() => {});
		}

		if (typeof caches !== 'undefined') {
			void caches
				.keys()
				.then((keys) => keys.forEach((k) => void caches.delete(k)))
				.catch(() => {});
		}
	} catch {
		// private windows and old browsers may refuse; there is nothing to remove there anyway
	}

	return {
		needRefresh: writable(false),
		offlineReady: writable(false),
		updateServiceWorker: async (_reload?: boolean) => {}
	};
}
