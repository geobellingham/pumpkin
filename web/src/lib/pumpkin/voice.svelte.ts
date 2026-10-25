/**
 * Hands-free voice chat for the phone: the browser's speech recognition hears you, the message is
 * sent through the normal chat flow, and the reply is read out sentence by sentence while it streams.
 * Everything runs on the phone, the computer does no audio work.
 */
import { chatStore } from '$lib/stores/chat/index.svelte';
import { conversationsStore } from '$lib/stores/conversations/index.svelte';

type Phase = 'idle' | 'listening' | 'thinking' | 'speaking';

interface RecognitionResult {
	0: { transcript: string };
	isFinal: boolean;
	length: number;
}
interface RecognitionEvent {
	resultIndex: number;
	results: ArrayLike<RecognitionResult>;
}
interface Recognition {
	continuous: boolean;
	interimResults: boolean;
	lang: string;
	maxAlternatives: number;
	onend: (() => void) | null;
	onerror: ((e: { error: string }) => void) | null;
	onresult: ((e: RecognitionEvent) => void) | null;
	abort(): void;
	start(): void;
	stop(): void;
}

const ABBREVIATION = /\b(mr|mrs|ms|dr|prof|vs|etc|e\.g|i\.e|st|no|fig)\.$/i;
const CODE_NOTICE = "I'm putting the code on the screen.";

export const VOICE_LANGUAGES: { code: string; label: string }[] = [
	{ code: 'en-US', label: 'English (US)' },
	{ code: 'en-GB', label: 'English (UK)' },
	{ code: 'bg-BG', label: 'Български' },
	{ code: 'zh-CN', label: '中文 (普通话)' }
];

/** Turns markdown into something worth saying out loud. */
export function speakable(text: string): string {
	const cleaned = text
		.replace(/<think>[\s\S]*?<\/think>/g, '')
		.replace(/!?\[([^\]]*)\]\([^)]*\)/g, '$1')
		.replace(/https?:\/\/\S+/g, 'link')
		.replace(/[*_`#>|~]+/g, ' ')
		.replace(/\p{Extended_Pictographic}/gu, '');

	return cleaned
		.split('\n')
		.map((l) =>
			l
				.replace(/^\s*([-+•]|\d+[.)])\s+/, '')
				.replace(/\s+/g, ' ')
				.trim()
		)
		.filter((l) => /[\p{L}\p{N}]/u.test(l))
		.map((l) => (/[.!?…:;,。！？]$/.test(l) ? l : l + '.'))
		.join(' ');
}

/** Splits a streaming reply into whole sentences, skipping code blocks. */
export class SpeechChunker {
	private buf = '';
	private inCode = false;
	private saidCode = false;
	private first = true;

	private split(text: string): [string[], string] {
		const out: string[] = [];
		const re = /([.!?…。！？]+["')\]]*)(\s+|(?=[一-鿿]))|(\n+)/gu;
		let start = 0;
		let m: RegExpExecArray | null;

		while ((m = re.exec(text))) {
			const end = m[1] ? m.index + m[1].length : m.index;
			const cand = text.slice(start, end).trim();

			if (ABBREVIATION.test(cand) || /^\d+[.)]$/.test(cand)) continue;

			if (cand.length < (this.first ? 6 : 24) && !/[。！？]$/.test(cand)) continue;

			const said = speakable(cand);

			if (said) {
				out.push(said);
				this.first = false;
			}

			start = re.lastIndex;
		}

		return [out, text.slice(start)];
	}

	feed(delta: string): string[] {
		this.buf += delta;
		let out: string[] = [];

		for (;;) {
			if (this.inCode) {
				const i = this.buf.indexOf('```');

				if (i < 0) {
					this.buf = this.buf.slice(-2);

					break;
				}

				this.buf = this.buf.slice(i + 3);
				this.inCode = false;

				continue;
			}

			const j = this.buf.indexOf('```');

			if (j >= 0) {
				const [said, rest] = this.split(this.buf.slice(0, j) + '\n');

				out = out.concat(said);

				const tail = speakable(rest);

				if (tail) out.push(tail);

				this.buf = this.buf.slice(j + 3);
				this.inCode = true;

				if (!this.saidCode) {
					this.saidCode = true;
					out.push(CODE_NOTICE);
				}

				continue;
			}

			const hold = (this.buf.match(/`*$/)?.[0] ?? '').length;
			const body = this.buf.slice(0, this.buf.length - hold);
			const [said, rest] = this.split(body);

			out = out.concat(said);
			this.buf = rest + this.buf.slice(this.buf.length - hold);

			break;
		}

		return out;
	}

	flush(): string[] {
		if (this.inCode) {
			this.buf = '';

			return [];
		}

		const s = speakable(this.buf.replace(/`/g, ''));

		this.buf = '';

		return s ? [s] : [];
	}
}

function load(key: string, fallback: string): string {
	try {
		return localStorage.getItem('pumpkin.voice.' + key) ?? fallback;
	} catch {
		return fallback;
	}
}

function save(key: string, value: string): void {
	try {
		localStorage.setItem('pumpkin.voice.' + key, value);
	} catch {
		/* ignore */
	}
}

class StudioVoice {
	active = $state(false);
	phase = $state<Phase>('idle');
	heard = $state('');
	problem = $state('');
	lang = $state('en-US');
	/** Also read replies to typed messages aloud. */
	readTyped = $state(false);
	supported = $state(false);
	canSpeak = $state(false);

	private rec: Recognition | null = null;
	private pending = 0;
	private sentByVoice = false;
	private tracking: { id: string; spoken: number; chunker: SpeechChunker } | null = null;
	private initialised = false;

	init(): void {
		if (this.initialised || typeof window === 'undefined') return;

		this.initialised = true;

		const w = window as unknown as Record<string, unknown>;

		this.supported = !!(w.SpeechRecognition || w.webkitSpeechRecognition);
		this.canSpeak = 'speechSynthesis' in window;
		this.lang = load('lang', navigator.language?.startsWith('bg') ? 'bg-BG' : 'en-US');
		this.readTyped = load('readTyped', '0') === '1';

		$effect.root(() => {
			$effect(() => {
				const convId = conversationsStore.activeConversation?.id ?? '';
				const s = chatStore.chatStreamingStates.get(convId);

				this.onStream(s ? { id: s.messageId, text: s.response } : null);
			});
		});

		if (this.canSpeak) {
			// voices load lazily on some browsers
			speechSynthesis.getVoices();
			speechSynthesis.addEventListener?.('voiceschanged', () => speechSynthesis.getVoices());
		}
	}

	setLang(code: string): void {
		this.lang = code;
		save('lang', code);

		if (this.active && this.phase === 'listening') {
			this.abortRec();
			this.listen();
		}
	}

	setReadTyped(on: boolean): void {
		this.readTyped = on;
		save('readTyped', on ? '1' : '0');

		if (!on && !this.active) this.stopSpeaking();
	}

	/** The mic button: start, interrupt the reply, or end the conversation. */
	press(): void {
		if (!this.active) return this.begin();

		if (this.phase === 'speaking') {
			this.stopSpeaking();
			this.listen();

			return;
		}

		this.end();
	}

	begin(): void {
		this.problem = '';

		if (!this.supported) {
			this.problem = 'This browser has no speech recognition. Use Safari on iPhone or Chrome on Android.';

			return;
		}

		this.active = true;

		if (this.canSpeak) {
			// an empty utterance inside the tap unlocks speech on iPhone
			try {
				speechSynthesis.speak(new SpeechSynthesisUtterance(''));
			} catch {
				/* ignore */
			}
		}

		this.listen();
	}

	end(): void {
		this.active = false;
		this.abortRec();
		this.stopSpeaking();
		this.phase = 'idle';
		this.heard = '';
		this.sentByVoice = false;
	}

	testVoice(): void {
		if (!this.canSpeak) return;

		this.stopSpeaking();
		this.say('Hello. This is how replies will sound.');
	}

	private listen(): void {
		const w = window as unknown as Record<string, new () => Recognition>;
		const Ctor = w.SpeechRecognition || w.webkitSpeechRecognition;

		if (!Ctor || !this.active || this.rec) return;

		const rec = new Ctor();

		this.rec = rec;
		this.phase = 'listening';
		this.heard = '';
		rec.lang = this.lang;
		rec.continuous = false;
		rec.interimResults = true;
		rec.maxAlternatives = 1;

		let finalText = '';

		rec.onresult = (e) => {
			let interim = '';

			for (let i = e.resultIndex; i < e.results.length; i++) {
				const r = e.results[i];

				if (r.isFinal) finalText += r[0].transcript;
				else interim += r[0].transcript;
			}

			this.heard = (finalText + interim).trim();
		};
		rec.onerror = (e) => {
			if (e.error === 'not-allowed' || e.error === 'service-not-allowed') {
				this.problem = "The microphone is blocked. Allow it for this site in your browser's settings.";
				this.end();
			} else if (e.error === 'audio-capture') {
				this.problem = 'No microphone was found. Plug one in or pick it in your system settings.';
				this.end();
			} else if (e.error === 'network') {
				// Chrome sends the sound to its own servers to turn it into words
				this.problem = 'Speech recognition needs the internet in this browser, and it could not reach it.';
				this.end();
			}
		};
		rec.onend = () => {
			this.rec = null;

			const text = finalText.trim();

			if (!this.active) return;

			if (text) {
				void this.send(text);

				return;
			}

			// heard nothing, keep listening
			if (this.phase === 'listening') setTimeout(() => this.listen(), 200);
		};

		try {
			rec.start();
		} catch {
			this.rec = null;
			setTimeout(() => this.listen(), 500);
		}
	}

	private abortRec(): void {
		const r = this.rec;

		this.rec = null;

		if (r) {
			r.onend = null;

			try {
				r.abort();
			} catch {
				/* ignore */
			}
		}
	}

	private async send(text: string): Promise<void> {
		this.phase = 'thinking';
		this.heard = text;
		this.sentByVoice = true;

		if (chatStore.isLoading || chatStore.isStreaming()) {
			await chatStore.stopGeneration();
		}

		try {
			await chatStore.sendMessage(text);
		} catch {
			this.problem = 'Sending failed. Check that a model is running.';
		}

		// a reply that never streamed (error, empty answer) must not leave us stuck
		setTimeout(() => {
			if (this.active && this.phase === 'thinking' && !chatStore.isLoading && !chatStore.isStreaming()) {
				this.listen();
			}
		}, 1500);
	}

	private get shouldSpeak(): boolean {
		return this.canSpeak && ((this.active && this.sentByVoice) || this.readTyped);
	}

	private onStream(s: { id: string; text: string } | null): void {
		const t = this.tracking;

		if (t && (!s || s.id !== t.id)) {
			// the message finished (or the agent moved on to the next one): say the rest
			const msg = conversationsStore.activeMessages.find((m) => m.id === t.id);
			const full = msg?.content ?? '';

			if (this.shouldSpeak) {
				if (full.length > t.spoken) t.chunker.feed(full.slice(t.spoken)).forEach((x) => this.say(x));

				t.chunker.flush().forEach((x) => this.say(x));
			}

			this.tracking = null;
		}

		if (!s) {
			this.afterSpeech();

			return;
		}

		if (!this.tracking) this.tracking = { chunker: new SpeechChunker(), id: s.id, spoken: 0 };

		const cur = this.tracking;

		if (s.text.length > cur.spoken) {
			const delta = s.text.slice(cur.spoken);

			cur.spoken = s.text.length;

			if (this.shouldSpeak) cur.chunker.feed(delta).forEach((x) => this.say(x));
		}
	}

	private pickVoice(): SpeechSynthesisVoice | null {
		const voices = speechSynthesis.getVoices();
		const lang = this.lang.toLowerCase();
		const base = lang.split('-')[0];
		const norm = (v: SpeechSynthesisVoice) => v.lang.toLowerCase().replace('_', '-');
		const exact = voices.filter((v) => norm(v) === lang);
		const pool = exact.length ? exact : voices.filter((v) => norm(v).startsWith(base));

		return (
			pool.find((v) => /premium|enhanced|neural|natural/i.test(v.name)) ??
			pool.find((v) => /google|siri|samantha|daniel/i.test(v.name)) ??
			pool[0] ??
			null
		);
	}

	private say(text: string): void {
		if (!this.canSpeak || !text) return;

		const u = new SpeechSynthesisUtterance(text);

		u.lang = this.lang;

		const v = this.pickVoice();

		if (v) u.voice = v;

		this.pending++;
		this.abortRec();

		if (this.active) this.phase = 'speaking';

		u.onend = u.onerror = () => {
			this.pending = Math.max(0, this.pending - 1);
			this.afterSpeech();
		};
		speechSynthesis.speak(u);
	}

	private stopSpeaking(): void {
		this.pending = 0;

		if (this.canSpeak) {
			try {
				speechSynthesis.cancel();
			} catch {
				/* ignore */
			}
		}
	}

	private afterSpeech(): void {
		if (this.pending > 0 || this.tracking) return;

		if (chatStore.isStreaming()) return;

		if (this.active && (this.phase === 'speaking' || this.phase === 'thinking') && !this.rec) {
			// the reply has been said, hand the turn back
			if (this.phase === 'thinking' && chatStore.isLoading) return;

			this.listen();
		}
	}
}

export const pumpkinVoice = new StudioVoice();
