<script lang="ts">
	import '../app.css';
	import type { LayoutProps } from './$types';
	let { children }: LayoutProps = $props();
</script>

<header style="border-bottom:1px solid var(--line);padding:0.75rem 1rem;display:flex;gap:1rem;align-items:center">
	<span class="chat-name" style="font-size:1.25rem">Duckie</span>
	<span class="mono" style="color:var(--comment);font-size:0.8125rem">a buyer agent, not a shop</span>
	<a href="/contacts" class="mono" style="margin-left:auto">Shops</a>
</header>
{@render children()}
