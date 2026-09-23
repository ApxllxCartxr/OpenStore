<script lang="ts">
	import { formatRupees } from '$lib/pricing.ts';
	import { BRAND } from '$lib/brand.ts';
	import { BUCKET_LABEL, type Bucket } from '$lib/availability.ts';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	// One selection per axis. `Add` stays disabled until the picker resolves to
	// exactly ONE Catalogue Item — a group is never sellable and never a cart
	// line, so there is nothing to add until a variant exists.
	let chosen = $state<Record<string, string>>({});

	const axes = $derived(
		Object.entries((data.group.option_axes ?? {}) as Record<string, string[]>)
	);

	const resolved = $derived(
		data.items.find((item) =>
			axes.every(([axis]) => item.options[axis] === chosen[axis])
		) ?? (axes.length === 0 ? data.items[0] : undefined)
	);

	/** A combination with no Catalogue Item was never made. It renders as
	 *  unavailable rather than being hidden — hiding it makes the picker look
	 *  broken, and 404ing it makes the shop look broken. */
	function combinationExists(axis: string, value: string): boolean {
		const candidate: Record<string, string> = { ...chosen, [axis]: value };
		return data.items.some((item) =>
			axes.every(([a]) => !candidate[a] || item.options[a] === candidate[a])
		);
	}

	/** The photographs are of the variant, so the gallery follows the picker and
	 *  falls back to the group's cover before a variant is resolved. */
	const gallery = $derived(
		(resolved?.media?.length ? resolved.media : (data.group.media ?? [])) as string[]
	);
	let shown = $state(0);
	$effect(() => {
		gallery.length;
		shown = 0;
	});

	const jsonLd = $derived(
		JSON.stringify({
			'@context': 'https://schema.org',
			'@type': 'ProductGroup',
			name: data.group.name,
			description: data.group.description,
			productGroupID: data.group.id,
			variesBy: axes.map(([axis]) => axis),
			hasVariant: data.items.map((item) => ({
				'@type': 'Product',
				sku: item.sku,
				name: item.name,
				image: item.media ?? [],
				offers: {
					'@type': 'Offer',
					priceCurrency: 'INR',
					// Tax-inclusive, as India requires in feeds: an Offer.price that
					// excludes GST understates every listing.
					price: (item.price_minor / 100).toFixed(2),
					availability:
						item.bucket === 'sold-out'
							? 'https://schema.org/OutOfStock'
							: 'https://schema.org/InStock'
				}
			}))
		})
	);
</script>

<svelte:head>
	<title>{data.group.name} at {BRAND.name}</title>
	{@html `<script type="application/ld+json">${jsonLd}<\/script>`}
</svelte:head>

<nav class="crumbs meta"><a href="/shop">Shop</a> / <span>{data.group.name}</span></nav>

<article class="product">
	<div class="gallery">
		{#if gallery.length}
			<figure class="shot">
				<img
					src={gallery[shown]}
					alt={resolved?.name ?? data.group.name}
					width="900"
					height="900"
					fetchpriority="high"
				/>
			</figure>
			{#if gallery.length > 1}
				<div class="option-row">
					{#each gallery as src, i (src)}
						<button
							type="button"
							class="option thumb"
							aria-pressed={shown === i}
							aria-label="View photo {i + 1}"
							onclick={() => (shown = i)}
						>
							<img src={src} alt="" width="120" height="120" />
						</button>
					{/each}
				</div>
			{/if}
		{/if}
	</div>

	<div class="buy">
		<h1>{data.group.name}</h1>
		<p class="lede">{data.group.description}</p>

		{#each axes as [axis, values] (axis)}
			<fieldset>
				<legend>{axis}</legend>
				<div class="option-row">
					{#each values as value (value)}
						{@const exists = combinationExists(axis, value)}
						<button
							type="button"
							class="option"
							aria-pressed={chosen[axis] === value}
							data-unavailable={!exists}
							disabled={!exists}
							onclick={() => (chosen = { ...chosen, [axis]: value })}
						>
							{value}{!exists ? ' (not made)' : ''}
						</button>
					{/each}
				</div>
			</fieldset>
		{/each}

		{#if resolved}
			<div class="card price-card">
				<div class="price-row">
					<span class="price">{formatRupees(resolved.price_minor)}</span>
					<span class="bucket" data-b={resolved.bucket}
						>{BUCKET_LABEL[resolved.bucket as Bucket]}</span
					>
				</div>
				<div class="sku meta">{resolved.sku}</div>
			</div>
		{:else}
			<p class="card meta">
				Pick {axes.map(([a]) => a).join(' and ')} to see the price.
			</p>
		{/if}

		{#if data.addons.length}
			<section class="addons">
				<h2>Add-ons</h2>
				<ul>
					{#each data.addons as addon (addon.sku)}
						<li>
							<span>{addon.name}</span>
							<span class="price">{formatRupees(addon.price_minor)}</span>
						</li>
					{/each}
				</ul>
				<p class="meta">
					Add-ons attach to a line and are never sold alone. Gift-wrap on a charm-bar seat is
					taxed as that service, not as goods.
				</p>
			</section>
		{/if}
	</div>
</article>

<section class="agent-panel">
	<h2 style="font-size:1.25rem">Buy via your agent</h2>
	<p style="margin-top:var(--s-1)">
		Paste this card into any agent that speaks the protocol. It can browse and build a basket; it
		cannot spend. Every purchase ends with you approving the exact amount on this domain.
	</p>
	<code>{data.cardUrl}</code>
</section>

<style>
	.crumbs {
		margin-bottom: var(--s-3);
	}
	.crumbs a {
		text-decoration: none;
	}
	.crumbs a:hover {
		text-decoration: underline;
	}

	.product {
		display: grid;
		grid-template-columns: 1fr;
		gap: var(--s-5);
		align-items: start;
	}

	.shot {
		margin: 0 0 var(--s-2);
		overflow: hidden;
		border: 1px solid var(--line);
		border-radius: var(--r-surface);
		background: var(--bg-sunken);
		aspect-ratio: 1;
	}
	.shot img {
		width: 100%;
		height: 100%;
		object-fit: cover;
		display: block;
	}
	.thumb {
		width: 3.5rem;
		height: 3.5rem;
		padding: 0;
		overflow: hidden;
	}
	.thumb img {
		width: 100%;
		height: 100%;
		object-fit: cover;
	}

	.buy {
		display: flex;
		flex-direction: column;
		gap: var(--s-3);
	}
	fieldset {
		border: 0;
		padding: 0;
		margin: 0;
	}
	legend {
		padding: 0;
		font-size: var(--t-caption);
		color: var(--muted);
	}

	.price-card {
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
	}
	.price-row {
		display: flex;
		align-items: baseline;
		justify-content: space-between;
		gap: var(--s-2);
	}
	.price-row .price {
		font-size: 1.5rem;
		font-weight: 600;
	}

	.addons h2 {
		font-size: 1.0625rem;
		margin-bottom: var(--s-1);
	}
	/* Name left, money right, one hairline between rows and none around the
	   group: a rule under every row turns a three-item list into a ledger. */
	.addons ul {
		list-style: none;
		margin: 0 0 var(--s-2);
		padding: 0;
	}
	.addons li {
		display: flex;
		justify-content: space-between;
		gap: var(--s-3);
		padding-block: var(--s-1);
	}
	.addons li + li {
		border-top: 1px solid var(--line);
	}

	@media (min-width: 900px) {
		.product {
			grid-template-columns: 1.05fr 0.95fr;
			gap: var(--s-6);
		}
		.gallery {
			position: sticky;
			top: calc(68px + var(--s-4));
		}
	}
</style>
