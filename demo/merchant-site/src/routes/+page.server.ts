import { sql } from '$lib/db.ts';
import { bucketFor } from '$lib/availability.ts';

export async function load() {
	const rows = await sql<
		{ slug: string; name: string; sku: string; price_minor: bigint; available: number; low: number }[]
	>`SELECT g.slug, g.name, i.sku, i.price_minor, s.available, i.low_stock_threshold AS low
	    FROM product_groups g
	    JOIN catalogue_items i ON i.group_id = g.id
	    JOIN stock s ON s.sku = i.sku
	   WHERE g.status = 'active' AND i.status = 'active'
	   ORDER BY g.name, i.sku`;

	const featured = new Map<string, { slug: string; name: string; from: number; bucket: string }>();
	for (const row of rows) {
		const existing = featured.get(row.slug);
		const price = Number(row.price_minor);
		const bucket = bucketFor(row.available, row.low);
		if (!existing) {
			featured.set(row.slug, { slug: row.slug, name: row.name, from: price, bucket });
		} else {
			existing.from = Math.min(existing.from, price);
			// A group reads in-stock when ANY of its items is.
			if (bucket === 'in-stock') existing.bucket = 'in-stock';
			else if (bucket === 'low-stock' && existing.bucket === 'sold-out') existing.bucket = bucket;
		}
	}
	return { featured: [...featured.values()] };
}
