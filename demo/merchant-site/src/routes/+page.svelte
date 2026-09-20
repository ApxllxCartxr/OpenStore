<script lang="ts">
	import { formatRupees } from '$lib/pricing.ts';
	import { BUCKET_LABEL, type Bucket } from '$lib/availability.ts';
	import type { PageProps } from './$types';
	let { data }: PageProps = $props();
</script>

<svelte:head><title>SpoiledDuckie — accessories</title></svelte:head>

<p style="max-width:60ch">
	Small accessories, made in Bengaluru. Buy here, or send your own agent — this shop
	publishes an agent card, and every purchase still ends with you approving the exact amount.
</p>

<div class="grid" style="margin-top:2rem">
	{#each data.featured as group (group.slug)}
		<a class="card" href="/p/{group.slug}" style="text-decoration:none">
			<strong>{group.name}</strong>
			<div class="price" style="margin-top:0.5rem">from {formatRupees(group.from)}</div>
			<div class="bucket" data-b={group.bucket}>{BUCKET_LABEL[group.bucket as Bucket]}</div>
		</a>
	{/each}
</div>
