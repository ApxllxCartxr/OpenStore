import { sql } from '$lib/db.ts';
import { bucketFor, groupBucket, type Bucket } from '$lib/availability.ts';

export async function load({ url }) {
	const query = (url.searchParams.get('q') ?? '').trim();
	const availability = url.searchParams.get('availability') ?? '';

	const rows = await sql<
		{ slug: string; name: string; tags: string[]; sku: string; price_minor: bigint; available: number; low: number }[]
	>`SELECT g.slug, g.name, g.tags, i.sku, i.price_minor, s.available, i.low_stock_threshold AS low
	    FROM product_groups g
	    JOIN catalogue_items i ON i.group_id = g.id
	    JOIN stock s ON s.sku = i.sku
	   WHERE g.status = 'active' AND i.status = 'active'
	     AND (${query} = '' OR g.name ILIKE ${'%' + query + '%'})
	   ORDER BY g.name, i.sku`;

	const groups = new Map<string, { slug: string; name: string; from: number; buckets: Bucket[] }>();
	for (const row of rows) {
		const entry = groups.get(row.slug) ?? {
			slug: row.slug,
			name: row.name,
			from: Number(row.price_minor),
			buckets: []
		};
		entry.from = Math.min(entry.from, Number(row.price_minor));
		entry.buckets.push(bucketFor(row.available, row.low));
		groups.set(row.slug, entry);
	}

	let results = [...groups.values()].map((g) => ({
		slug: g.slug,
		name: g.name,
		from: g.from,
		bucket: groupBucket(g.buckets)
	}));
	if (availability) results = results.filter((r) => r.bucket === availability);

	// Ordered results, always: an agent that re-reads a shop must see the same
	// order, or "verbatim" stops meaning anything (SPEC §11).
	results.sort((a, b) => (a.name < b.name ? -1 : 1));
	return { results, query, availability };
}
