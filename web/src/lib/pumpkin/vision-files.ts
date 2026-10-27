import type { LocalModel } from './types';

const slash = (p: string) => p.replace(/\\/g, '/');

/** A short name for the folder a file is in: "unsloth/gemma-3-4b-it-GGUF" from a Hugging Face cache path,
 * otherwise the last folder (or the last two when the last one alone says little). */
export function folderLabel(folder: string): string {
	const parts = slash(folder).split('/').filter(Boolean);
	const hf = parts.findIndex((p) => p.startsWith('models--'));

	if (hf >= 0) return parts[hf].slice('models--'.length).replace(/--/g, '/');

	const last = parts[parts.length - 1] ?? folder;

	return /^(models?|gguf|mmproj|vision|[0-9a-f]{12,})$/i.test(last) && parts.length > 1 ? `${parts[parts.length - 2]}/${last}` : last;
}

/** Whether this vision file was made for the model: the one the library paired with it, or one in its folder. */
export function madeFor(proj: LocalModel, model: LocalModel | undefined): boolean {
	if (!model) return false;

	return proj.path === model.mmproj || slash(proj.folder) === slash(model.folder);
}

/** The Vision file picker: the ones made for the chosen model first and marked, then the rest by folder. */
export function visionOptions(projs: LocalModel[], model: LocalModel | undefined) {
	const sorted = [...projs].sort((a, b) => {
		const ma = madeFor(a, model) ? 0 : 1;
		const mb = madeFor(b, model) ? 0 : 1;

		return ma - mb || folderLabel(a.folder).localeCompare(folderLabel(b.folder)) || a.file.localeCompare(b.file);
	});

	return [
		{ label: 'None', value: '' },
		...sorted.map((p) => ({
			label: `${p.file} · ${folderLabel(p.folder)}${madeFor(p, model) ? ' (made for this model)' : ''}`,
			value: p.path
		}))
	];
}
