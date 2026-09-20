<script lang="ts">
	import '../app.css';
	import type { LayoutProps } from './$types';
	let { children }: LayoutProps = $props();
</script>

<header class="wrap">
	<a href="/" class="shop-name" style="font-size:1.75rem;text-decoration:none">SpoiledDuckie</a>
	<nav style="display:flex;gap:1rem;margin-top:0.5rem">
		<a href="/shop">Shop</a>
		<a href="/lookup">Find my order</a>
	</nav>
</header>
<main class="wrap">{@render children()}</main>
