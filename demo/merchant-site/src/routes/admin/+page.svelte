<script lang="ts">
	import type { PageProps } from './$types';
	let { data }: PageProps = $props();
</script>

<svelte:head><title>Dashboard — shop ops</title></svelte:head>
<div class="grid mono" style="grid-template-columns:repeat(auto-fit,minmax(10rem,1fr))">
	<div class="card"><div style="color:var(--comment)">Open orders</div><div style="font-size:2rem">{data.open}</div></div>
	<div class="card"><div style="color:var(--comment)">Refund requested</div><div style="font-size:2rem">{data.refundRequested}</div></div>
	<div class="card"><div style="color:var(--comment)">Failed</div><div style="font-size:2rem">{data.failed}</div></div>
	<div class="card"><div style="color:var(--comment)">Low stock</div><div style="font-size:2rem">{data.lowStock.length}</div></div>
</div>

{#if data.lowStock.length}
	<h2 style="font-size:1rem;margin-top:2rem">Low stock</h2>
	<table class="mono" style="width:100%;border-collapse:collapse">
		<thead><tr><th style="text-align:left">SKU</th><th style="text-align:right">on hand</th><th style="text-align:right">threshold</th></tr></thead>
		<tbody>
			{#each data.lowStock as row (row.sku)}
				<tr style="border-top:1px solid var(--line)">
					<td>{row.sku}</td><td style="text-align:right">{row.available}</td><td style="text-align:right">{row.low}</td>
				</tr>
			{/each}
		</tbody>
	</table>
{/if}
