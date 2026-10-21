export type Status = 'offline' | 'off' | 'loading' | 'ready' | 'busy' | 'error';

export interface Preset {
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
	think_budget?: number | null;
	tools?: boolean;
	web?: boolean;
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

export interface State {
	update?: { available: boolean; latest: string };
	agent: { busy: boolean; job: string | null; log_seq: number };
	downloads: number;
	has_models: boolean;
	has_server: boolean;
	installs: Install[];
	live: {
		gpu_busy?: number | null;
		ram_total?: number;
		ram_used?: number;
		unified?: boolean;
		vram_total?: number;
		vram_used?: number;
	};
	metrics: { predicted_tokens_seconds?: number | null };
	migrated: string[];
	persona: string;
	persona_prompt: string;
	remote: string;
	server: {
		error: string | null;
		port: number;
		preset: Preset | null;
		state: 'off' | 'loading' | 'ready' | 'error' | 'external';
		uptime: number | null;
	};
	setup_done: boolean;
	version: string;
}

export interface Fit {
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
	download_dir: string;
	hf_token: string;
	keep_awake: boolean;
	llama_port: number;
	llama_server: string;
	max_steps: number;
	models_dirs: string[];
	notifications: boolean;
	ntfy_server: string;
	ntfy_topic: string;
	persona: string;
	port: number;
	remote: string;
	reports_dir: string;
	sandbox_image: string;
	setup_done: boolean;
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
		ok: boolean;
		path: string;
		version: string | null;
	} | null;
	log: string;
	path: string | null;
}

export interface Hardware {
	backend: string;
	cpu: { model: string; physical_cores: number };
	disk: number | null;
	gpus: { name: string; vendor: string; vram_total: number; integrated?: boolean }[];
	main_gpu: { name: string; unified?: boolean; vendor: string; vram_total: number } | null;
	ram_total: number;
	vulkan: boolean;
}
