<script lang="ts">
	import '../../app.css';
	import '../../admin.css';
	import { BRAND } from '$lib/brand.ts';
	import { page } from '$app/state';
	import type { LayoutProps } from './$types';
	let { data, children }: LayoutProps = $props();

	// Shop ops only: catalogue, stock, orders. Keys, provider secrets, policy,
	// exposure and receipts live in the sidecar's /agentic console, so this demo
	// admin stays replaceable.
	const nav = [
		{ href: '/admin', label: 'Dashboard' },
		{ href: '/admin/orders', label: 'Orders' },
		{ href: '/admin/catalogue', label: 'Catalogue' },
		{ href: '/admin/inventory', label: 'Inventory' }
	];

	const titles: Record<string, { title: string; sub: string }> = {
		'/admin': { title: 'Dashboard', sub: 'What needs attention in the shop today' },
		'/admin/orders': { title: 'Orders', sub: 'Every order, however it was placed' },
		'/admin/catalogue': { title: 'Catalogue', sub: 'Prices and what is listed' },
		'/admin/inventory': { title: 'Inventory', sub: 'On hand, held, and what is running out' }
	};
	const head = $derived(
		titles[page.url.pathname] ?? { title: 'Shop ops', sub: '' }
	);
</script>

{#if data.operator}
	<div class="admin-shell">
		<div class="admin-side">
			<div class="admin-brand">
				<b>{BRAND.name}</b><span>shop ops</span>
			</div>
			<nav>
				{#each nav as item (item.href)}
					<a href={item.href} aria-current={page.url.pathname === item.href ? 'page' : undefined}
						>{item.label}</a
					>
				{/each}
			</nav>
			<div class="admin-who mono">{data.operator}</div>
		</div>
		<main class="admin-main">
			<div class="admin-head">
				<h1>{head.title}</h1>
				{#if head.sub}<p>{head.sub}</p>{/if}
			</div>
			{@render children()}
		</main>
	</div>
{:else}
	<main class="wrap">{@render children()}</main>
{/if}
