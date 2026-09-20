<script lang="ts">
	import type { PageProps } from './$types';
	let { data }: PageProps = $props();

	// Permission modal state. A spend step never inherits a standing approval,
	// so this opens fresh every time (ADR-0008).
	let pending = $state<{ tool: string; scope: string; request: unknown; spend: boolean } | null>(null);
</script>

<svelte:head><title>Duckie — buyer agent</title></svelte:head>

<div class="thread">
	{#each data.thread.messages as message (message.at + message.role)}
		<div class="row" data-who={message.role}>
			<div class="bubble">{message.text}</div>
		</div>
	{/each}

	{#each data.thread.toolCalls as call (call.at + call.name)}
		<details class="tool">
			<summary class="mono">{call.name}</summary>
			<!-- The exact request JSON. A card that summarised could be wrong, and
			     the Consumer would have no way to tell. -->
			<pre class="mono">{JSON.stringify(JSON.parse(call.request), null, 2)}</pre>
			{#if call.response}
				<pre class="mono" style="color:var(--comment)">{JSON.stringify(JSON.parse(call.response), null, 2)}</pre>
			{/if}
		</details>
	{/each}

	<p class="mono" style="color:var(--comment);font-size:0.8125rem;margin-top:2rem">
		driver: {data.driver} · {data.tools.length} tools · this agent holds no payment credential
	</p>
</div>

{#if pending}
	<div class="modal-scrim" role="dialog" aria-modal="true" aria-label="Permission">
		<div class="modal">
			<h2 style="font-size:1rem;margin-top:0">
				{pending.spend ? 'This shop may build a paid basket' : `Allow ${pending.tool}?`}
			</h2>
			{#if pending.spend}
				<!-- Grants a SCOPE and never an amount: no total exists yet, and a
				     modal that reads like an amount approval teaches the Consumer
				     to click through the tap that is one. -->
				<p>No amount is being approved here. You will see the exact total, signed by the shop,
					and approve it on the shop’s own page.</p>
			{/if}
			<pre class="mono" style="background:var(--bg-sunken);padding:0.75rem;overflow-x:auto">{JSON.stringify(pending.request, null, 2)}</pre>
			<div style="display:flex;gap:0.5rem;flex-wrap:wrap">
				<button onclick={() => (pending = null)}>Allow once</button>
				{#if !pending.spend}
					<button class="secondary" onclick={() => (pending = null)}>Always allow (reads only)</button>
				{/if}
				<button class="secondary" onclick={() => (pending = null)}>Decline</button>
			</div>
		</div>
	</div>
{/if}
