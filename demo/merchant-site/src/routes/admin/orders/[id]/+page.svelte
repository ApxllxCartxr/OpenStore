<script lang="ts">
	import { formatRupees } from '$lib/pricing.ts';
	import type { PageProps } from './$types';
	let { data, form }: PageProps = $props();

	const order = $derived(data.order);
	const isCod = $derived(order.payment_method === 'cash-on-delivery');
	const canRefund = $derived(['paid', 'completed'].includes(order.status));
	const remaining = $derived(order.total_minor - order.refunded_minor);
	// Dispatch is available from `confirmed` onward and independent of status —
	// a COD order dispatches before `paid` exists at all.
	const canDispatch = $derived(['confirmed', 'paid', 'refunded', 'completed'].includes(order.status));
</script>

<svelte:head><title>{order.order_id} — shop ops</title></svelte:head>

<h1 class="mono" style="font-weight:600">{order.order_id}</h1>
<p class="mono">
	{order.status} · {order.payment_method} · {formatRupees(order.total_minor)}
	{#if order.refunded_minor}
		<span style="color:var(--accent)">· Refunded {formatRupees(order.refunded_minor)} of {formatRupees(order.total_minor)}</span>
	{/if}
</p>

{#if form?.message}
	<p class="mono card" style="border-color:var(--accent-2)">{form.message}</p>
{/if}

<section style="margin-top:1.5rem">
	<h2 style="font-size:1rem">Dispatch</h2>
	{#if order.invoice_number}
		<p class="mono">Invoice {order.invoice_number}
			{#if order.tracking_number}· {order.carrier} {order.tracking_number}{/if}</p>
	{/if}
	{#if canDispatch}
		<form method="POST" action="?/dispatch" style="display:flex;gap:0.5rem;flex-wrap:wrap" class="mono">
			<input type="hidden" name="csrf" value={data.csrf} />
			<input name="tracking_number" placeholder="Tracking number (optional)"
				style="min-height:44px;padding:0 0.75rem;border:1px solid var(--line);background:var(--bg-raised);color:inherit" />
			<input name="carrier" placeholder="Carrier"
				style="min-height:44px;padding:0 0.75rem;border:1px solid var(--line);background:var(--bg-raised);color:inherit" />
			<button type="submit">{order.invoice_number ? 'Update dispatch' : 'Record dispatch'}</button>
		</form>
		<p style="color:var(--comment);font-size:0.8125rem;max-width:60ch">
			The invoice number is assigned on the first dispatch and never re-issued. A COD order can
			dispatch while still <code>confirmed</code>, before it is paid — the invoice follows the
			goods, not the money.
		</p>
	{:else}
		<p style="color:var(--comment)">An order at <code>{order.status}</code> has shipped nothing.</p>
	{/if}
</section>

{#if isCod && order.status === 'confirmed'}
	<section style="margin-top:1.5rem">
		<h2 style="font-size:1rem">Cash on delivery</h2>
		<div style="display:flex;gap:0.5rem;flex-wrap:wrap">
			<form method="POST" action="?/collect">
				<input type="hidden" name="csrf" value={data.csrf} />
				<button type="submit">Record collection</button>
			</form>
			<form method="POST" action="?/rto">
				<input type="hidden" name="csrf" value={data.csrf} />
				<button type="submit" style="background:var(--bg-sunken);color:var(--fg)">Record RTO</button>
			</form>
		</div>
		<p style="color:var(--comment);font-size:0.8125rem;max-width:60ch">
			Collection writes the capture. An RTO cancels with reason <code>rto</code> and returns the
			stock, writing no ledger entry at all — nothing moved.
		</p>
	</section>
{/if}

{#if canRefund && remaining > 0}
	<section style="margin-top:1.5rem">
		<h2 style="font-size:1rem">Refund</h2>
		<form method="POST" action="?/refund" style="display:grid;gap:0.5rem;max-width:28rem" class="mono">
			<input type="hidden" name="csrf" value={data.csrf} />
			<label>Amount (paise, at most {remaining})
				<input name="amount_minor" type="number" min="1" max={remaining} value={remaining} required
					style="width:100%;min-height:44px;padding:0 0.75rem;border:1px solid var(--line);background:var(--bg-raised);color:inherit" /></label>
			<label>Reason<input name="reason"
				style="width:100%;min-height:44px;padding:0 0.75rem;border:1px solid var(--line);background:var(--bg-raised);color:inherit" /></label>
			<fieldset style="border:1px solid var(--line);padding:0.75rem">
				<legend style="color:var(--comment)">Return stock to sale</legend>
				{#each order.lines as line (line.sku)}
					<label style="display:block;min-height:44px">
						<input type="checkbox" name="restock" value={line.sku} /> {line.sku}
					</label>
				{/each}
			</fieldset>
			<button type="submit">Refund</button>
		</form>
	</section>
{/if}

{#if ['pending', 'confirmed'].includes(order.status)}
	<form method="POST" action="?/reject" style="margin-top:1rem">
		<input type="hidden" name="csrf" value={data.csrf} />
		<input name="reason" placeholder="Reason" class="mono"
			style="min-height:44px;padding:0 0.75rem;border:1px solid var(--line);background:var(--bg-raised);color:inherit" />
		<button type="submit" style="background:var(--bg-sunken);color:var(--fg)">Reject order</button>
	</form>
{/if}

<section style="margin-top:2rem">
	<h2 style="font-size:1rem">Timeline</h2>
	<ul class="mono" style="padding-left:1.25rem">
		{#if order.cart_hash}<li>Transcript ref <code>{order.cart_hash.slice(0, 16)}…</code></li>{/if}
		{#each data.moves as move (move.at + move.sku)}
			<li>{move.at} · {move.channel} · {move.sku} {move.delta > 0 ? '+' : ''}{move.delta}</li>
		{/each}
		{#each data.refunds as refund (refund.created_at)}
			<li>{refund.created_at} · refund {formatRupees(refund.amount_minor)} — {refund.reason}</li>
		{/each}
		{#each data.notifications as note (note.at + note.kind)}
			<li>{note.at} · notified: {note.kind}</li>
		{/each}
		{#if order.receipt_id}<li><a href="/receipt/{order.receipt_id}">Receipt</a></li>{/if}
	</ul>
</section>
