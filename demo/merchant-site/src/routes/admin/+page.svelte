<script lang="ts">
	import { formatRupees } from '$lib/pricing.ts';
	import { columns } from '$lib/chart.ts';
	import type { PageProps } from './$types';
	let { data }: PageProps = $props();

	const plot = $derived(columns(data.series));
	const sold = $derived(data.agentOrders + data.directOrders);
	const agentShare = $derived(sold ? Math.round((data.agentOrders / sold) * 100) : 0);
</script>

<svelte:head><title>Dashboard, shop ops</title></svelte:head>

<div class="tiles">
	<div class="tile">
		<div class="k">Revenue</div>
		<div class="v">{formatRupees(data.revenue)}</div>
		<div class="s">{sold} order{sold === 1 ? '' : 's'} paid</div>
	</div>
	<div class="tile">
		<div class="k">Through an agent</div>
		<div class="v">{agentShare}%</div>
		<div class="s">{data.agentOrders} of {sold}</div>
	</div>
	<div class="tile">
		<div class="k">Open orders</div>
		<div class="v">{data.open}</div>
		<div class="s">pending or confirmed</div>
	</div>
	<div class="tile">
		<div class="k">Needs a decision</div>
		<div class="v">{data.refundRequested + data.failed}</div>
		<div class="s">{data.refundRequested} refund asked, {data.failed} failed</div>
	</div>
</div>

<section>
	<h2>Revenue, last 14 days</h2>
	{#if plot.empty}
		<p class="chart-empty">No paid orders in the last 14 days.</p>
	{:else}
		<svg class="chart" viewBox="0 0 {plot.width} {plot.height}" role="img"
			aria-label="Revenue per day over the last 14 days">
			<line class="grid-line" x1="8" y1="16" x2={plot.width - 8} y2="16" />
			<line class="grid-line" x1="8" y1={plot.baseline} x2={plot.width - 8} y2={plot.baseline} />
			{#each plot.columns as col (col.label)}
				{#if col.value > 0}
					<path class="mark" d={col.path}>
						<title>{col.label}: {formatRupees(col.value)}</title>
					</path>
				{/if}
				<!-- Only the peak is labelled. A number over every column is chaos
				     and goes unread; the rest are in the table view below. -->
				{#if col.peak}
					<text class="val" x={col.x + col.w / 2} y={col.y - 5} text-anchor="middle">
						{formatRupees(col.value)}
					</text>
				{/if}
				{#if col.tick}
					<text class="tick" x={col.x + col.w / 2} y={plot.height - 6} text-anchor="middle">
						{col.label}
					</text>
				{/if}
			{/each}
		</svg>
	{/if}
	<p class="meta" style="margin-top:var(--s-2)">
		Paid, completed and refunded orders, by the day they were placed. A day with no column
		took no money, rather than having no reading.
	</p>
</section>

<section>
	<h2>Low stock</h2>
	{#if data.lowStock.length}
		<table class="mono">
			<thead>
				<tr><th>SKU</th><th class="num">on hand</th><th class="num">threshold</th></tr>
			</thead>
			<tbody>
				{#each data.lowStock as row (row.sku)}
					<tr>
						<td>{row.sku}</td>
						<td class="num">{row.available}</td>
						<td class="num">{row.low}</td>
					</tr>
				{/each}
			</tbody>
		</table>
	{:else}
		<p class="meta">Nothing at or below its threshold.</p>
	{/if}
</section>
