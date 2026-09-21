<script lang="ts">
	import { enhance } from '$app/forms';
	import type { PageProps } from './$types';
	let { data }: PageProps = $props();

	/** Results worth showing as something other than JSON: what the shop
	 *  returned, rendered as the shop returned it. No total is computed here. */
	type Parsed = { name: string; request: any; response: any; at: string };
	const calls = $derived(
		data.thread.toolCalls.map((c) => ({
			name: c.name,
			at: c.at,
			request: JSON.parse(c.request),
			response: c.response ? JSON.parse(c.response) : null
		})) as Parsed[]
	);

	const latest = $derived(calls.length ? calls[calls.length - 1] : null);
	const approval = $derived(
		calls.filter((c) => c.response?.approve_url).at(-1)?.response ?? null
	);

	function rupees(minor: number): string {
		return `₹${(minor / 100).toFixed(2)}`;
	}
	/** Shop images are root-relative on the shop's own origin, and this page is
	 *  a different origin — so they are resolved against the shop's. */
	function onShop(src: string): string {
		return src.startsWith('http') ? src : `http://${data.shop?.domain}${src}`;
	}
	let sending = $state(false);
</script>

<svelte:head><title>Duckie — buyer agent</title></svelte:head>

<div class="thread">
	<header style="display:flex;gap:0.75rem;align-items:baseline;flex-wrap:wrap;margin-bottom:1rem">
		<strong>Duckie</strong>
		{#if data.shop}
			<span class="mono" style="color:var(--comment);font-size:0.8125rem">
				talking to {data.shop.name} · {data.shop.domain}
			</span>
		{:else}
			<span class="mono" style="color:var(--accent);font-size:0.8125rem">
				no shop yet — <a href="/contacts">add one</a>
			</span>
		{/if}
	</header>

	{#each data.thread.messages as message (message.at + message.role)}
		<div class="row" data-who={message.role}>
			<div class="bubble">{message.text}</div>
		</div>
	{/each}

	{#if latest?.response?.results?.length}
		<div class="grid" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(9rem,1fr));gap:0.75rem;margin:0.5rem 0 1rem">
			{#each latest.response.results as result (result.group)}
				<div class="tool" style="padding:0.5rem">
					{#if result.image}
						<img src={onShop(result.image)} alt={result.name} loading="lazy"
							style="width:100%;aspect-ratio:1;object-fit:cover;background:var(--raised)" />
					{/if}
					<div style="margin-top:0.5rem"><strong>{result.name}</strong></div>
					<div class="mono" style="font-size:0.8125rem;color:var(--comment)">
						from {rupees(result.from_minor)} · {result.availability}
					</div>
				</div>
			{/each}
		</div>
	{/if}

	{#if latest?.response?.quote}
		<!-- The shop's own Quote, line for line. Nothing on this page adds up. -->
		<table class="tool" style="width:100%;border-collapse:collapse;margin-bottom:1rem">
			<tbody>
				{#each latest.response.quote.lines as line (line.sku)}
					<tr><td class="mono">{line.sku} × {line.qty}</td>
						<td class="mono" style="text-align:right">{rupees(line.line_total_minor)}</td></tr>
				{/each}
				{#each latest.response.quote.tax_lines as tax (tax.kind)}
					<tr style="color:var(--comment)"><td class="mono">{tax.label}</td>
						<td class="mono" style="text-align:right">{rupees(tax.amount_minor)}</td></tr>
				{/each}
				<tr><td class="mono"><strong>Total</strong></td>
					<td class="mono" style="text-align:right"><strong>{rupees(latest.response.quote.total_minor)}</strong></td></tr>
			</tbody>
		</table>
	{/if}

	{#if approval}
		<div class="tool" style="border-color:var(--accent);padding:1rem;margin-bottom:1rem">
			<p style="margin-top:0">The shop wants {rupees(approval.total_minor)} for
				<code class="mono">{approval.order_id}</code>.</p>
			<p class="mono" style="font-size:0.8125rem;color:var(--comment)">
				You approve this on the shop’s own page. I hold no payment credential and
				cannot complete it.
			</p>
			<a class="tap" href={approval.approve_url} rel="noopener"
				style="display:inline-block;padding:0.75rem 1rem;border:1px solid var(--accent);text-decoration:none">
				Approve {rupees(approval.total_minor)} at {data.shop?.domain}
			</a>
		</div>
	{/if}

	{#if data.pending}
		<div class="tool" style="border-color:var(--accent);padding:1rem;margin-bottom:1rem">
			<h2 style="font-size:1rem;margin-top:0">
				{data.pending.name === 'start-checkout' || data.pending.name === 'place-order'
					? 'This step builds a paid basket'
					: `Allow ${data.pending.name}?`}
			</h2>
			{#if data.pending.name === 'start-checkout' || data.pending.name === 'place-order'}
				<!-- Grants a SCOPE and never an amount: no total exists yet, and a
				     prompt that reads like an amount approval teaches the Consumer
				     to click through the tap that is one. -->
				<p>No amount is approved here. You will see the exact total, from the shop, and
					approve it on the shop’s own page.</p>
			{/if}
			<pre class="mono" style="background:var(--bg-sunken);padding:0.75rem;overflow-x:auto">{JSON.stringify(data.pending.args, null, 2)}</pre>
			<form method="POST" action="?/decide" use:enhance style="display:flex;gap:0.5rem;flex-wrap:wrap">
				<button name="verdict" value="allow-once" type="submit">Allow once</button>
				{#if data.pending.name === 'search' || data.pending.name === 'read-item' || data.pending.name === 'order-status'}
					<button class="secondary" name="verdict" value="always" type="submit">Always allow (reads only)</button>
				{/if}
				<button class="secondary" name="verdict" value="declined" type="submit">Decline</button>
			</form>
		</div>
	{/if}

	{#each calls as call (call.at + call.name)}
		<details class="tool">
			<summary class="mono">{call.name}</summary>
			<!-- The exact request JSON. A card that summarised could be wrong, and
			     the Consumer would have no way to tell. -->
			<pre class="mono">{JSON.stringify(call.request, null, 2)}</pre>
			{#if call.response}
				<pre class="mono" style="color:var(--comment)">{JSON.stringify(call.response, null, 2)}</pre>
			{/if}
		</details>
	{/each}

	<form method="POST" action="?/send" use:enhance={() => { sending = true; return async ({ update }) => { await update(); sending = false; }; }}
		style="display:flex;gap:0.5rem;margin-top:1.5rem;position:sticky;bottom:0;background:var(--bg);padding:0.75rem 0">
		<input name="text" placeholder="What are you looking for?" autocomplete="off" required
			style="flex:1;min-height:44px;padding:0 0.75rem;border:1px solid var(--line);background:var(--raised);color:inherit" />
		<button type="submit" disabled={sending}>{sending ? 'Asking…' : 'Send'}</button>
	</form>

	<div style="display:flex;gap:1rem;align-items:baseline;flex-wrap:wrap">
		<p class="mono" style="color:var(--comment);font-size:0.8125rem;margin:0">
			driver: {data.driver} · {data.tools.length} tools · this agent holds no payment credential
		</p>
		<form method="POST" action="?/reset" use:enhance>
			<button class="secondary" type="submit" style="font-size:0.8125rem">Reset thread</button>
		</form>
	</div>
</div>
