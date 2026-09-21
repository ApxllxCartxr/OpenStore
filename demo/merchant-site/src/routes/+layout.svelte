<script lang="ts">
	import '../app.css';
	import { BRAND } from '$lib/brand.ts';
	import { page } from '$app/state';
	import type { LayoutProps } from './$types';
	let { children }: LayoutProps = $props();

	const nav = [
		{ href: '/shop', label: 'Shop' },
		{ href: '/lookup', label: 'Find my order' }
	];
</script>

<!-- The one token a shop overrides. Hex-validated in `brand.ts`, because this
     value is interpolated into a style block; empty means the default accent. -->
<svelte:head>
	{@html BRAND.accent ? `<style>:root{--accent:${BRAND.accent};}</style>` : ''}
</svelte:head>

<header class="masthead">
	<div class="wrap masthead-inner">
		<a href="/" class="shop-name" style="font-size:1.6rem;text-decoration:none">{BRAND.name}</a>
		<nav>
			{#each nav as item (item.href)}
				<a href={item.href} aria-current={page.url.pathname === item.href ? 'page' : undefined}
					>{item.label}</a
				>
			{/each}
		</nav>
	</div>
</header>

<main class="wrap">{@render children()}</main>

<footer class="sitefoot">
	<div class="wrap sitefoot-inner">
		<span>{BRAND.tagline}</span>
		<span class="mono">
			<a href="/.well-known/agent-commerce.json">Agent card</a>
		</span>
	</div>
</footer>
