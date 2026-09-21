<script lang="ts">
	import { formatRupees } from '$lib/pricing.ts';
	import { BRAND } from '$lib/brand.ts';
	import type { PageProps } from './$types';
	let { form }: PageProps = $props();
</script>

<svelte:head><title>Find my order — {BRAND.name}</title></svelte:head>

<h1 style="font-weight:600">Find my order</h1>
<p style="max-width:60ch">
	Use the link from your confirmation. If you only have the order number, we also need the email
	or phone the order was placed with — the number alone is not a password.
</p>

<form method="POST" style="display:grid;gap:0.75rem;max-width:32rem;margin-top:1.5rem">
	<label>Order link token
		<input name="token" class="mono"
			style="width:100%;min-height:44px;padding:0 0.75rem;border:1px solid var(--line);background:var(--bg-raised);color:inherit" />
	</label>
	<p style="color:var(--comment);margin:0">or</p>
	<label>Order number
		<input name="order_number" class="mono"
			style="width:100%;min-height:44px;padding:0 0.75rem;border:1px solid var(--line);background:var(--bg-raised);color:inherit" />
	</label>
	<label>Email or phone on the order
		<input name="contact"
			style="width:100%;min-height:44px;padding:0 0.75rem;border:1px solid var(--line);background:var(--bg-raised);color:inherit" />
	</label>
	<button type="submit">Look up</button>
</form>

{#if form?.message}
	<p class="mono" style="color:var(--accent);margin-top:1rem">{form.message}</p>
{/if}

{#if form?.order}
	<div class="card mono" style="margin-top:1.5rem;max-width:32rem">
		<div>{form.order.order_id}</div>
		<div>{form.order.status}</div>
		<div class="price">{formatRupees(form.order.total_minor)}</div>
		{#if form.order.tracking_number}
			<div>{form.order.carrier} · {form.order.tracking_number}</div>
		{/if}
		{#if form.order.invoice_number}<div>Invoice {form.order.invoice_number}</div>{/if}
		{#if form.order.receipt_id}
			<a href="/receipt/{form.order.receipt_id}">Open receipt</a>
		{/if}
	</div>
{/if}
