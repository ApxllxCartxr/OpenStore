<script lang="ts">
	import { formatRupees } from '$lib/pricing.ts';
	import type { PageProps } from './$types';
	let { data, form }: PageProps = $props();
</script>

<svelte:head><title>Catalogue — shop ops</title></svelte:head>
{#if form?.message}<p class="mono card">{form.message}</p>{/if}

{#each data.matrix as group (group.group)}
	<section style="margin-bottom:2rem">
		<h2 style="font-size:1rem">{group.name}</h2>
		<table class="mono" style="width:100%;border-collapse:collapse">
			<thead><tr>
				{#each group.axes as axis (axis)}<th style="text-align:left">{axis}</th>{/each}
				<th style="text-align:left">SKU</th><th style="text-align:right">price</th>
				<th style="text-align:right">stock</th><th style="text-align:left">HSN</th>
				<th style="text-align:right">GST</th><th></th>
			</tr></thead>
			<tbody>
				{#each group.rows as row (JSON.stringify(row.combination))}
					<tr style="border-top:1px solid var(--line)">
						{#each group.axes as axis (axis)}<td>{row.combination[axis]}</td>{/each}
						{#if row.item}
							<td>{row.item.sku}</td>
							<td colspan="5">
								<form method="POST" action="?/update" style="display:flex;gap:0.5rem;justify-content:flex-end">
									<input type="hidden" name="csrf" value={data.csrf} />
									<input type="hidden" name="sku" value={row.item.sku} />
									<input name="price_minor" type="number" min="0" value={row.item.price_minor} aria-label="price in paise"
										style="width:7rem;min-height:44px;padding:0 0.5rem;border:1px solid var(--line);background:var(--bg-raised);color:inherit" />
									<input name="available" type="number" min="0" value={row.item.available} aria-label="stock"
										style="width:5rem;min-height:44px;padding:0 0.5rem;border:1px solid var(--line);background:var(--bg-raised);color:inherit" />
									<span style="align-self:center;color:var(--comment)">{row.item.hsn_sac} · {row.item.gst_rate_bp / 100}%</span>
									<button type="submit">Save</button>
								</form>
							</td>
						{:else}
							<!-- Absent, not zero-stocked: this combination was never made. -->
							<td colspan="6" style="color:var(--comment)">never made — no SKU, no stock row</td>
						{/if}
					</tr>
				{/each}
			</tbody>
		</table>
	</section>
{/each}
