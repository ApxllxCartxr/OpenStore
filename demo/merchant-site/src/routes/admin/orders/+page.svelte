<script lang="ts">
	import { formatRupees } from '$lib/pricing.ts';
	import type { PageProps } from './$types';
	let { data }: PageProps = $props();
</script>

<svelte:head><title>Orders — shop ops</title></svelte:head>
<form method="GET" style="display:flex;gap:0.5rem;margin-bottom:1rem" class="mono">
	<input name="q" value={data.query} placeholder="Order id"
		style="min-height:44px;padding:0 0.75rem;border:1px solid var(--line);background:var(--raised);color:inherit" />
	<select name="status" style="min-height:44px;border:1px solid var(--line);background:var(--raised);color:inherit">
		<option value="">Any status</option>
		{#each ['pending', 'confirmed', 'paid', 'cancelled', 'expired', 'failed', 'refunded', 'completed'] as s (s)}
			<option value={s} selected={data.status === s}>{s}</option>
		{/each}
	</select>
	<button type="submit">Filter</button>
</form>

<table class="mono" style="width:100%;border-collapse:collapse">
	<thead><tr>
		<th style="text-align:left">order</th><th style="text-align:left">status</th>
		<th style="text-align:right">total</th><th style="text-align:right">refunded</th>
		<th style="text-align:left">method</th><th style="text-align:left">invoice</th>
	</tr></thead>
	<tbody>
		{#each data.orders as order (order.order_id)}
			<tr style="border-top:1px solid var(--line)">
				<td><a href="/admin/orders/{order.order_id}">{order.order_id}</a></td>
				<td>{order.status}</td>
				<td style="text-align:right">{formatRupees(order.total_minor)}</td>
				<td style="text-align:right">{order.refunded_minor ? formatRupees(order.refunded_minor) : '—'}</td>
				<td>{order.payment_method}</td>
				<td>{order.invoice_number ?? '—'}</td>
			</tr>
		{:else}
			<tr><td colspan="6" style="color:var(--comment);padding-top:1rem">No orders yet. The sidecar creates them through door 7.</td></tr>
		{/each}
	</tbody>
</table>
