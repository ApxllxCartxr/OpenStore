<script lang="ts">
	import { formatRupees } from '$lib/pricing.ts';
	import { BRAND } from '$lib/brand.ts';
	import { BUCKET_LABEL, type Bucket } from '$lib/availability.ts';
	import type { PageProps } from './$types';
	let { data }: PageProps = $props();
</script>

<svelte:head><title>Shop at {BRAND.name}</title></svelte:head>

<header class="shop-head">
	<h1>Shop</h1>
	<p class="meta">
		{data.results.length}
		{data.results.length === 1 ? 'product' : 'products'}
	</p>
</header>

<form method="GET" class="filters">
	<label class="field">
		<span>Search</span>
		<input name="q" value={data.query} placeholder="Tote, charm bar" />
	</label>
	<label class="field">
		<span>Availability</span>
		<select name="availability">
			<option value="">Any</option>
			<option value="in-stock" selected={data.availability === 'in-stock'}>In stock</option>
			<option value="low-stock" selected={data.availability === 'low-stock'}>Low stock</option>
			<option value="sold-out" selected={data.availability === 'sold-out'}>Sold out</option>
		</select>
	</label>
	<button type="submit">Filter</button>
</form>

{#if data.results.length === 0}
	<div class="empty">
		<h2 style="font-size:1.0625rem">Nothing matches that search.</h2>
		<p class="meta">Try a shorter word, or clear the filters to see the whole catalogue.</p>
		<a class="button secondary" href="/shop">See everything</a>
	</div>
{:else}
	<div class="grid">
		{#each data.results as group (group.slug)}
			<a class="tile" href="/p/{group.slug}">
				<div class="tile-frame">
					{#if group.cover}
						<img src={group.cover} alt={group.name} loading="lazy" width="600" height="600" />
					{/if}
				</div>
				<span class="tile-name">{group.name}</span>
				<span class="price">from {formatRupees(group.from)}</span>
				<span class="bucket" data-b={group.bucket}>{BUCKET_LABEL[group.bucket as Bucket]}</span>
			</a>
		{/each}
	</div>
{/if}

<style>
	.shop-head {
		display: flex;
		align-items: baseline;
		justify-content: space-between;
		gap: var(--s-3);
		flex-wrap: wrap;
		margin-bottom: var(--s-3);
	}

	.filters {
		display: flex;
		gap: var(--s-2);
		flex-wrap: wrap;
		align-items: flex-end;
		padding-bottom: var(--s-4);
		margin-bottom: var(--s-4);
		border-bottom: 1px solid var(--line);
	}
	.filters .field {
		flex: 1 1 12rem;
		min-width: 0;
	}

	.empty {
		display: flex;
		flex-direction: column;
		align-items: flex-start;
		gap: var(--s-2);
		padding: var(--s-6) 0;
	}
</style>
