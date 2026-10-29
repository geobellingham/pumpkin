export type Status = 'offline' | 'off' | 'loading' | 'ready' | 'busy' | 'error';

export interface Preset {
	connectors?: string[] | null;
	ctx: number;
	desc?: string;
	extra?: string;
	fitt?: number;
	id: string;
	idle_minutes?: number;
	kv?: string;
	mmproj?: string;
	model: string;
	name: string;
	sandbox?: boolean;
	spec?: boolean;
	spec_result?: SpecResult;
	think_budget?: number | null;
	tools?: boolean;
	web?: boolean;
}

export interface SpecResult {
	at: number;
	off: { accepted: number | null; edit: number; new: number };
	on: { accepted: number | null; edit: number; new: number };
	text: string;
	verdict: 'faster' | 'same' | 'slower';
}

export interface CompareRow {
	answer?: string;
	error?: string;
	gen?: number;
	id: string;
	load?: number;
	name: string;
	prompt?: number;
	seconds?: number;
	thinking?: string;
	tokens?: number;
	vram?: number | null;
}

export interface Job {
	done: number;
	error: string | null;
	id: string;
	kind: string;
	phase?: string;
	preset?: string;
	prompt?: string;
	result?: unknown;
	results?: CompareRow[];
	state: 'running' | 'done' | 'error';
	total: number;
}

export interface Install {
	done: number;
	error: string | null;
	id: string;
	model: string;
	phase: string;
	state: 'running' | 'done' | 'error';
	total: number;
}

export interface ActivityItem {
	context?: { total: number; used: number };
	detail?: string | null;
	id: string;
	kind: 'agent' | 'code' | 'download' | 'image' | 'job' | 'model' | 'vision';
	label: string;
	note?: string;
	progress?: number | null;
	since?: number | null;
	state: 'idle' | 'loading' | 'paused' | 'waiting' | 'working';
	title: string;
}

export interface ChecklistStep {
	done: boolean;
	href: string;
	id: string;
	optional: boolean;
	skipped: boolean;
	text: string;
	title: string;
}

export interface State {
	activity: ActivityItem[];
	now: number;
	update?: { available: boolean; latest: string };
	memory_note?: { kind: 'swap' | 'disk'; text: string } | null;
	llama_update?: { available: boolean; current: string | null; latest: string | null; stale: boolean } | null;
	agent: { busy: boolean; job: string | null; log_seq: number };
	downloads: number;
	has_models: boolean;
	has_server: boolean;
	installs: Install[];
	live: {
		gpu_busy?: number | null;
		model_ram?: number;
		ram_total?: number;
		ram_used?: number;
		unified?: boolean;
		vram_total?: number;
		vram_used?: number;
	};
	metrics: {
		n_tokens_max?: number | null;
		predicted_tokens_seconds?: number | null;
		prompt_tokens_seconds?: number | null;
		tokens_predicted_total?: number | null;
	};
	migrated: string[];
	persona: string;
	persona_prompt: string;
	platform: string;
	remote: string;
	server: {
		error: string | null;
		fix?: 'update_llama' | null;
		notice?: string | null;
		port: number;
		preset: Preset | null;
		state: 'off' | 'loading' | 'ready' | 'error' | 'external';
		uptime: number | null;
	};
	setup_done: boolean;
	version: string;
}

export interface Fit {
	cpu?: boolean;
	phone?: boolean;
	ctx?: number;
	kv_gb: number;
	level: 'gpu' | 'moe' | 'split' | 'tight' | 'no';
	need_gb: number;
	text: string;
}

export interface LocalModel {
	complete: boolean;
	file: string;
	fit?: Fit;
	folder: string;
	id: string;
	info: {
		arch?: string;
		ctx_train?: number;
		error?: string;
		experts?: number;
		name?: string;
		size_label?: string;
	};
	kind: 'model' | 'mmproj';
	mmproj?: string | null;
	mtime: number;
	parts: number;
	path: string;
	quant: string | null;
	size_gb: number;
}

export interface HfFile {
	files: string[];
	fit?: Fit;
	kind: 'model' | 'mmproj';
	name: string;
	quant: string | null;
	size_gb: number;
}

export interface CatalogModel {
	blurb: string;
	ctx: number;
	id: string;
	installed?: boolean;
	likely: 'gpu' | 'ram' | 'no';
	min_memory_gb: number;
	name: string;
	plan?: Plan | null;
	repo: string;
	tags: string[];
	vision?: boolean;
}

export interface Plan {
	file: HfFile | null;
	fit: Fit | null;
	id: string;
	mmproj: HfFile | null;
	size_gb: number;
	text: string;
}

export interface Download {
	current: string | null;
	dest: string;
	done: number;
	error: string | null;
	files: string[];
	id: string;
	repo: string;
	speed: number;
	state: 'queued' | 'downloading' | 'done' | 'error' | 'cancelled';
	total: number;
}

export interface Persona {
	id: string;
	name: string;
	prompt: string;
}

export interface Prompt {
	id: string;
	prompt: string;
	title: string;
}

export interface Task {
	days: string;
	enabled: boolean;
	id: string;
	last_error?: string;
	last_report?: string;
	last_run?: string;
	name: string;
	preset: string;
	prompt: string;
	stop_after?: boolean;
	time: string;
}

export interface Check {
	area: string;
	command: string;
	detail: string;
	fix: string;
	status: 'ok' | 'warn' | 'fail';
	title: string;
}

export interface Settings {
	autostart_model: boolean;
	check_updates: boolean;
	code_max_steps: number;
	code_max_tokens: number;
	checklist_hidden: boolean;
	compact_chat: boolean;
	doc_folders: string[];
	doc_index: boolean;
	download_dir: string;
	hf_token: string;
	keep_awake: boolean;
	keep_context: boolean;
	vision_relay: boolean;
	vision_model: string;
	vision_keep_min: number;
	llama_port: number;
	llama_server: string;
	llama_channel: 'release' | 'newest';
	llama_updates: 'off' | 'notify' | 'auto';
	max_steps: number;
	memory: boolean;
	models_dirs: string[];
	notifications: boolean;
	web_private: boolean;
	artifacts: boolean;
	ntfy_server: string;
	ntfy_topic: string;
	persona: string;
	port: number;
	remote: string;
	reports_dir: string;
	sandbox_image: string;
	setup_done: boolean;
	setup_skipped: string[];
	skills: boolean;
	threads: number;
	tool_chars: number;
}

export interface LlamaInfo {
	backend: string;
	platform?: string;
	candidates: string[];
	info: {
		backends: string[];
		error: string | null;
		flags: string[];
		gpu_error?: string | null;
		ok: boolean;
		path: string;
		version: string | null;
	} | null;
	log: string;
	path: string | null;
	update?: LlamaUpdate;
}

export interface LlamaUpdate {
	available: boolean;
	backend: string;
	channel: 'release' | 'newest';
	checked: number | null;
	current: string | null;
	job: { done: number; error: string | null; id: string; state: string; tag?: string; total: number } | null;
	latest: string | null;
	mode: 'off' | 'notify' | 'auto';
	own: boolean;
	own_older: string | null;
	own_older_path: string | null;
	pinned: string | null;
	previous: { path: string; version: string | null } | null;
	skipped: Record<string, string>;
}

export interface Hardware {
	backend: string;
	cpu: { model: string; physical_cores: number };
	disk: number | null;
	gpus: { name: string; vendor: string; vram_total: number; integrated?: boolean }[];
	main_gpu: { name: string; phone?: boolean; unified?: boolean; vendor: string; vram_total: number } | null;
	ram_total: number;
	vulkan: boolean;
}

export interface Skill {
	body: string;
	description: string;
	enabled: boolean;
	files: string[];
	name: string;
	source: 'builtin' | 'yours' | 'project';
}

export interface ConnectorField {
	default?: unknown;
	help?: string;
	id: string;
	kind: 'secret' | 'text' | 'path' | 'paths' | 'toggle' | 'words';
	label: string;
	placeholder?: string;
	required?: boolean;
}

export interface Connector {
	added: boolean;
	available: boolean;
	blurb: string;
	docs: string;
	enabled: boolean;
	fields: ConnectorField[];
	id: string;
	login: boolean;
	login_hint: string;
	missing: string[];
	name: string;
	needs: string;
	needs_name: string;
	needs_url: string;
	private: boolean;
	signed_in: number | null;
	test: { at: number; error: string | null; ok: boolean; tools: string[] } | null;
	values: Record<string, unknown>;
}

export interface ConnectorLogin {
	code: string;
	error: string;
	lines: string[];
	message: string;
	state: 'idle' | 'starting' | 'waiting' | 'done' | 'error' | 'cancelled';
	url: string;
}
