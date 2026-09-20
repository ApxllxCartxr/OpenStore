<script lang="ts">
	import type { PageProps } from './$types';
	let { data }: PageProps = $props();
	const channels = ['site-direct','agent-reserve','agent-commit','agent-release','refund','restock','admin-adjust','rto'];
</script>

<svelte:head><title>Inventory — shop ops</title></svelte:head>
<form method="GET" style="margin-bottom:1rem" class="mono">
	<select name="channel" style="min-height:44px;border:1px solid var(--line);background:var(--raised);color:inherit">
		<option value="">Every channel</option>
		{#each channels as c (c)}<option value={c} selected={data.channel === c}>{c}</option>{/each}
	</select>
	<button type="submit">Filter</button>
</form>

<table class="mono" style="width:100%;border-collapse:collapse">
	<thead><tr><th style="text-align:left">when</th><th style="text-align:left">SKU</th>
		<th style="text-align:right">delta</th><th style="text-align:left">channel</th>
		<th style="text-align:left">actor</th><th style="text-align:left">reason</th></tr></thead>
	<tbody>
		{#each data.moves as move (move.at + move.sku + move.delta)}
			<tr style="border-top:1px solid var(--line)">
				<td>{move.at}</td><td>{move.sku}</td>
				<td style="text-align:right;color:{move.delta < 0 ? 'var(--accent)' : 'var(--ok)'}">{move.delta > 0 ? '+' : ''}{move.delta}</td>
				<td>{move.channel}</td><td>{move.actor}</td><td>{move.reason}</td>
			</tr>
		{:else}
			<tr><td colspan="6" style="color:var(--comment);padding-top:1rem">No moves yet.</td></tr>
		{/each}
	</tbody>
</table>
