import tailwindcss from '@tailwindcss/vite';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
	// Tailwind v4, because the imported token layer is a Tailwind `@theme`
	// block (§4). The design system is taken whole rather than transcribed.
	plugins: [tailwindcss(), sveltekit()],
	server: { port: 3000, host: true }
});
