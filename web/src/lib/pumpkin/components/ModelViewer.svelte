<script lang="ts">
	// A 3D model (.glb) you can turn around with the mouse or a finger. three.js is loaded only when a model is shown,
	// so the rest of Pumpkin stays as light as it was.
	import { LoaderCircle, RotateCcw } from '@lucide/svelte';
	import { onDestroy, onMount } from 'svelte';

	let { class: className = '', src }: { class?: string; src: string } = $props();

	let box: HTMLDivElement;
	let state = $state<'loading' | 'ready' | 'error'>('loading');
	let error = $state('');
	let reset: (() => void) | null = null;
	let cleanup: (() => void) | null = null;

	onMount(() => {
		let gone = false;

		(async () => {
			try {
				const THREE = await import('three');
				const { GLTFLoader } = await import('three/examples/jsm/loaders/GLTFLoader.js');
				const { OrbitControls } = await import('three/examples/jsm/controls/OrbitControls.js');
				const { RoomEnvironment } = await import('three/examples/jsm/environments/RoomEnvironment.js');

				if (gone) return;

				const renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true });

				renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
				renderer.outputColorSpace = THREE.SRGBColorSpace;
				renderer.toneMapping = THREE.NeutralToneMapping;          // keeps the colours the model was made with
				renderer.toneMappingExposure = 1;
				box.appendChild(renderer.domElement);

				const scene = new THREE.Scene();
				const pmrem = new THREE.PMREMGenerator(renderer);
				const env = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;

				scene.environment = env;
				scene.environmentIntensity = 0.7;

				const sun = new THREE.DirectionalLight(0xffffff, 0.9);

				sun.position.set(3, 5, 4);
				scene.add(sun);

				const camera = new THREE.PerspectiveCamera(40, 1, 0.01, 100);
				const controls = new OrbitControls(camera, renderer.domElement);

				controls.enableDamping = true;
				controls.autoRotate = true;
				controls.autoRotateSpeed = 1.2;
				controls.addEventListener('start', () => (controls.autoRotate = false));

				const res = await fetch(src, { credentials: 'same-origin' });

				if (!res.ok) throw new Error(`The file could not be loaded (${res.status}).`);

				const gltf = await new GLTFLoader().parseAsync(await res.arrayBuffer(), '');

				if (gone) return;

				const model = gltf.scene;
				const bounds = new THREE.Box3().setFromObject(model);
				const size = bounds.getSize(new THREE.Vector3()).length() || 1;
				const center = bounds.getCenter(new THREE.Vector3());

				model.position.sub(center);
				scene.add(model);

				// far enough back that the whole model fits the view with a margin, seen a little from above
				const radius = size / 2;
				const dist = (radius / Math.sin(THREE.MathUtils.degToRad(camera.fov / 2))) * 1.1;
				const view = new THREE.Vector3(0.55, 0.38, 0.85).normalize().multiplyScalar(dist);

				reset = () => {
					camera.position.copy(view);
					camera.near = size / 100;
					camera.far = size * 100;
					camera.updateProjectionMatrix();
					controls.target.set(0, 0, 0);
					controls.autoRotate = true;
					controls.update();
				};
				reset();

				const resize = () => {
					const w = box.clientWidth || 300;
					const h = box.clientHeight || 300;

					renderer.setSize(w, h, false);
					renderer.domElement.style.width = '100%';
					renderer.domElement.style.height = '100%';
					camera.aspect = w / h;
					camera.updateProjectionMatrix();
				};
				const ro = new ResizeObserver(resize);

				ro.observe(box);
				resize();

				let frame = 0;
				const tick = () => {
					frame = requestAnimationFrame(tick);
					controls.update();
					renderer.render(scene, camera);
				};

				tick();
				state = 'ready';

				cleanup = () => {
					cancelAnimationFrame(frame);
					ro.disconnect();
					controls.dispose();
					scene.traverse((o) => {
						const mesh = o as unknown as { geometry?: { dispose(): void }; material?: unknown };

						mesh.geometry?.dispose();
						const mats = Array.isArray(mesh.material) ? mesh.material : mesh.material ? [mesh.material] : [];

						for (const m of mats as Array<Record<string, unknown> & { dispose(): void }>) {
							for (const v of Object.values(m)) {
								if (v && typeof v === 'object' && 'isTexture' in v) (v as { dispose(): void }).dispose();
							}
							m.dispose();
						}
					});
					env.dispose();
					pmrem.dispose();
					renderer.dispose();
					renderer.domElement.remove();
				};
			} catch (e) {
				state = 'error';
				error = e instanceof Error ? e.message : String(e);
			}
		})();

		return () => {
			gone = true;
		};
	});

	onDestroy(() => cleanup?.());
</script>

<div class={['relative aspect-square w-full max-w-md overflow-hidden rounded-xl border border-border/60 bg-muted/30', className]}>
	<div bind:this={box} class="absolute inset-0 touch-none"></div>
	{#if state === 'loading'}
		<div class="absolute inset-0 flex items-center justify-center"><LoaderCircle class="h-5 w-5 animate-spin text-muted-foreground" /></div>
	{:else if state === 'error'}
		<div class="absolute inset-0 flex items-center justify-center p-4 text-center text-sm text-muted-foreground">
			The 3D view did not open: {error}
		</div>
	{:else}
		<button
			aria-label="Reset the view"
			class="absolute right-2 bottom-2 rounded-full bg-background/80 p-2 text-muted-foreground backdrop-blur hover:text-foreground"
			onclick={() => reset?.()}
			type="button"
		>
			<RotateCcw class="h-4 w-4" />
		</button>
	{/if}
</div>
