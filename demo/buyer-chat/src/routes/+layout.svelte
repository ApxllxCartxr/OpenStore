<script lang="ts">
	import '../app.css';
	import type { LayoutProps } from './$types';
	let { children }: LayoutProps = $props();
</script>

<header class="topbar">
	<div class="topbar-inner">
		<span class="chat-name" style="font-size:1.25rem">Miro</span>
		<span class="meta">a buyer agent, not a shop</span>
		<!-- A plain download link, not an action: the whole thread — every
		     message and every tool call's exact request and response — as a
		     Markdown file. `download` so it saves instead of navigating. -->
		<a href="/transcript" download class="meta topbar-action" style="margin-left:auto">
			Export transcript
		</a>
		<a href="/contacts" class="meta">Shops</a>
	</div>
</header>
{@render children()}
