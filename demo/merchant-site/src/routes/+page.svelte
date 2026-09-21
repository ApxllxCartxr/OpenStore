<script lang="ts">
	import { formatRupees } from '$lib/pricing.ts';
	import { BUCKET_LABEL, type Bucket } from '$lib/availability.ts';
	import { BRAND } from '$lib/brand.ts';
	import type { PageProps } from './$types';
	let { data }: PageProps = $props();
</script>

<svelte:head><title>{BRAND.name}, {BRAND.blurb}</title></svelte:head>

<!-- Asymmetric split. The photograph carries the shop; the copy carries the one
     claim that makes this shop different from any other one. -->
<section class="hero">
	<div class="hero-copy">
		<h1>{BRAND.tagline}</h1>
		<p class="lede">
			Buy them here, or send an agent to do it. Either way, you approve the exact amount
			yourself.
		</p>
		<div class="hero-actions">
			<a class="button" href="/shop">Shop all</a>
			<a class="button secondary" href="/.well-known/agent-commerce.json">Agent card</a>
		</div>
	</div>

	{#if data.hero?.cover}
		<a class="hero-figure" href="/p/{data.hero.slug}" aria-label={data.hero.name}>
			<img
				src={data.hero.cover}
				alt={data.hero.name}
				width="900"
				height="1100"
				fetchpriority="high"
			/>
		</a>
	{/if}
</section>

<section class="band">
	<h2>In the shop</h2>
	<div class="grid" style="margin-top:var(--s-4)">
		{#each data.featured as group (group.slug)}
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
</section>

<section class="agent-panel">
	<h2 style="font-size:1.0625rem">Buying through an agent</h2>
	<p style="margin-top:var(--s-1)">
		This shop publishes a card any agent can read. It can search the catalogue and build a
		basket. It cannot spend: the last step is always you, approving one exact amount on this
		domain.
	</p>
	<code>{data.cardUrl}</code>
</section>

<style>
	.hero {
		display: grid;
		grid-template-columns: 1fr;
		gap: var(--s-5);
		align-items: center;
		padding-block: var(--s-4) var(--s-6);
	}
	.hero-copy {
		display: flex;
		flex-direction: column;
		gap: var(--s-3);
	}
	.hero h1 {
		/* The hero headline is the largest type on the page, but it is still the
		   body family: EB Garamond is spent once, on the name in the masthead. */
		font-size: clamp(2.1rem, 5.2vw, 3.4rem);
	}
	.hero-actions {
		display: flex;
		flex-wrap: wrap;
		gap: var(--s-2);
		margin-top: var(--s-1);
	}
	.hero-figure {
		display: block;
		overflow: hidden;
		border: 1px solid var(--line);
		border-radius: var(--r-surface);
		background: var(--bg-sunken);
		aspect-ratio: 4 / 5;
	}
	.hero-figure img {
		width: 100%;
		height: 100%;
		object-fit: cover;
		display: block;
		transition: transform 0.5s var(--ease);
	}
	.hero-figure:hover img {
		transform: scale(1.03);
	}

	.band {
		padding-block: var(--s-2);
	}

	/* Single column below 900px, which is where the split stops earning its
	   asymmetry and starts squeezing both halves. */
	@media (min-width: 900px) {
		.hero {
			grid-template-columns: 1.1fr 0.9fr;
			gap: var(--s-6);
			padding-block: var(--s-5) var(--s-7);
		}
	}
</style>
