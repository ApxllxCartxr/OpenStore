<script lang="ts">
	import { formatRupees } from '$lib/pricing.ts';
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
	<title>{data.group.name} — SpoiledDuckie</title>
	{@html `<script type="application/ld+json">${jsonLd}<\/script>`}
</svelte:head>

<h1 style="font-weight:600">{data.group.name}</h1>
<p style="max-width:60ch">{data.group.description}</p>

{#each axes as [axis, values] (axis)}
	<fieldset style="border:0;padding:0;margin:1rem 0">
		<legend style="padding:0;color:var(--comment);font-size:0.8125rem">{axis}</legend>
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
					{value}{!exists ? ' — not made' : ''}
				</button>
			{/each}
		</div>
	</fieldset>
{/each}

{#if resolved}
	<div class="card" style="margin-top:1rem">
		<div class="sku">{resolved.sku}</div>
		<div class="price" style="font-size:1.25rem">{formatRupees(resolved.price_minor)}</div>
		<div class="bucket" data-b={resolved.bucket}>{BUCKET_LABEL[resolved.bucket as Bucket]}</div>
	</div>
{:else}
	<p class="mono" style="color:var(--comment)">Pick {axes.map(([a]) => a).join(' and ')} to see the price.</p>
{/if}

{#if data.addons.length}
	<h2 style="font-size:1rem;margin-top:1.5rem">Add-ons</h2>
	<ul class="mono" style="padding-left:1.25rem">
		{#each data.addons as addon (addon.sku)}
			<li>{addon.name} — {formatRupees(addon.price_minor)}</li>
		{/each}
	</ul>
	<p style="color:var(--comment);font-size:0.8125rem;max-width:60ch">
		Add-ons attach to a line and are never sold alone. Gift-wrap on a charm-bar seat is taxed as
		that service, not as goods.
	</p>
{/if}

<section class="agent-panel">
	<h2 style="font-size:1rem;margin:0 0 0.5rem">Buy via your agent</h2>
	<p style="max-width:60ch;margin:0 0 0.75rem">
		Paste this card into any agent that speaks the protocol. It can browse and build a basket;
		it cannot spend. Every purchase ends with you approving the exact amount on this domain.
	</p>
	<code style="display:block;word-break:break-all;background:var(--paper);padding:0.75rem;border:1px solid var(--line)">{data.cardUrl}</code>
</section>
