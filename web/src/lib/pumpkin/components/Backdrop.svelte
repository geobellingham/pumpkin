<!-- The light behind the glass: three big blurred blobs that drift slowly, and a vignette. One for the whole app. -->
<div aria-hidden="true" class="pk-backdrop">
	<div class="pk-blob pk-blob-1"></div>
	<div class="pk-blob pk-blob-2"></div>
	<div class="pk-blob pk-blob-3"></div>
	<div class="pk-vignette"></div>
</div>

<style>
	.pk-blob-1 {
		left: -8%;
		top: -18%;
		width: 52%;
		height: 62%;
		background: #ff7a1a;
		opacity: 0.3;
	}

	.pk-blob-2 {
		right: -10%;
		top: 6%;
		width: 46%;
		height: 56%;
		background: #6d3df0;
		opacity: 0.28;
		animation-delay: -7s;
	}

	.pk-blob-3 {
		left: 28%;
		bottom: -26%;
		width: 50%;
		height: 56%;
		background: #0f9c94;
		opacity: 0.2;
		animation-delay: -13s;
	}

	/* light: warm earth tones under white glass */
	:global(html:not(.dark)) .pk-blob-1 {
		background: #ff9a52;
		opacity: 0.38;
	}

	:global(html:not(.dark)) .pk-blob-2 {
		background: #8a5a3b;
		opacity: 0.3;
	}

	:global(html:not(.dark)) .pk-blob-3 {
		background: #6f8a4b;
		opacity: 0.32;
	}
</style>
