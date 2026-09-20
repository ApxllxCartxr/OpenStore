<script lang="ts">
	import { formatRupees } from '$lib/pricing.ts';
	import { BUCKET_LABEL, type Bucket } from '$lib/availability.ts';
	import type { PageProps } from './$types';
	let { data }: PageProps = $props();
</script>

<svelte:head><title>Shop — SpoiledDuckie</title></svelte:head>

<form method="GET" style="display:flex;gap:0.5rem;flex-wrap:wrap;margin-bottom:1.5rem">
	<input name="q" value={data.query} placeholder="Search" aria-label="Search"
		style="min-height:44px;padding:0 0.75rem;border:1px solid var(--line);background:var(--raised);color:inherit" />
	<select name="availability" aria-label="Availability"
		style="min-height:44px;border:1px solid var(--line);background:var(--raised);color:inherit">
		<option value="">Any availability</option>
		<option value="in-stock" selected={data.availability === 'in-stock'}>In stock</option>
		<option value="low-stock" selected={data.availability === 'low-stock'}>Low stock</option>
		<option value="sold-out" selected={data.availability === 'sold-out'}>Sold out</option>
	</select>
	<button type="submit">Filter</button>
</form>

{#if data.results.length === 0}
	<p>Nothing matches that. Try a different search, or <a href="/shop">see everything</a>.</p>
{:else}
	<div class="grid">
		{#each data.results as group (group.slug)}
			<a class="card" href="/p/{group.slug}" style="text-decoration:none">
				<strong>{group.name}</strong>
				<div class="price" style="margin-top:0.5rem">from {formatRupees(group.from)}</div>
				<div class="bucket" data-b={group.bucket}>{BUCKET_LABEL[group.bucket as Bucket]}</div>
			</a>
		{/each}
	</div>
{/if}
