import { sql } from '$lib/db.ts';
import { bucketFor } from '$lib/availability.ts';

export async function load() {
	const rows = await sql<
		{
			slug: string;
			name: string;
			media: string[];
			item_media: string[];
			sku: string;
			price_minor: bigint;
			available: number;
			low: number;
		}[]
	>`SELECT g.slug, g.name, g.media, i.media AS item_media, i.sku, i.price_minor,
	         s.available, i.low_stock_threshold AS low
	    FROM product_groups g
	    JOIN catalogue_items i ON i.group_id = g.id
	    JOIN stock s ON s.sku = i.sku
	   WHERE g.status = 'active' AND i.status = 'active'
	   ORDER BY g.name, i.sku`;

	const featured = new Map<
		string,
		{ slug: string; name: string; cover: string | null; from: number; bucket: string }
	>();
	for (const row of rows) {
		const existing = featured.get(row.slug);
		const price = Number(row.price_minor);
		const bucket = bucketFor(row.available, row.low);
		if (!existing) {
			featured.set(row.slug, {
				slug: row.slug,
				name: row.name,
				cover: row.media?.[0] ?? null,
				from: price,
				bucket
			});
		} else {
			existing.from = Math.min(existing.from, price);
			// A group reads in-stock when ANY of its items is.
			if (bucket === 'in-stock') existing.bucket = 'in-stock';
			else if (bucket === 'low-stock' && existing.bucket === 'sold-out') existing.bucket = bucket;
		}
	}

	const groups = [...featured.values()];

	// The hero photograph comes from a VARIANT's second shot, not the group
	// cover: the covers are all spent on the tiles below, and a hero that
	// repeats the first tile reads as a rendering bug rather than a feature.
	// Falls back to any cover, then to nothing, so a catalogue seeded without
	// media renders a text hero instead of a broken image frame.
	const second = rows.find((r) => (r.item_media?.length ?? 0) > 1);
	const heroGroup = second ? featured.get(second.slug) : undefined;
	const hero = heroGroup
		? { slug: heroGroup.slug, name: heroGroup.name, cover: second!.item_media[1] }
		: (groups.find((g) => g.cover) ?? null);
	return {
		featured: groups,
		hero,
		cardUrl: `${process.env.OPENSTORE_PUBLIC_ORIGIN ?? 'http://spoiledduckie.localhost'}/.well-known/agent-commerce.json`
	};
}
