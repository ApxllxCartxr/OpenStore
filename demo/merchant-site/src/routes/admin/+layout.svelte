<script lang="ts">
	import '../../app.css';
	import type { LayoutProps } from './$types';
	let { data, children }: LayoutProps = $props();
</script>

<!-- Shop ops only: catalogue, stock, orders. Keys, provider secrets, policy,
     exposure and receipts live in the sidecar's /agentic console, so this demo
     admin stays replaceable. -->
<header class="wrap" style="border-bottom:1px solid var(--line)">
	<strong class="mono">SpoiledDuckie · shop ops</strong>
	{#if data.operator}
		<nav style="display:flex;gap:1rem;margin-top:0.5rem" class="mono">
			<a href="/admin">Dashboard</a>
			<a href="/admin/orders">Orders</a>
			<a href="/admin/catalogue">Catalogue</a>
			<a href="/admin/inventory">Inventory</a>
			<span style="margin-left:auto;color:var(--comment)">{data.operator}</span>
		</nav>
	{/if}
</header>
<main class="wrap">{@render children()}</main>
