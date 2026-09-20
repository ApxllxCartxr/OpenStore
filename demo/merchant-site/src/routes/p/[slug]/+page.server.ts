import { error } from '@sveltejs/kit';
import { sql } from '$lib/db.ts';
import { bucketFor, groupBucket, type Bucket } from '$lib/availability.ts';

export async function load({ params }) {
	const groups = await sql<
		{ id: string; slug: string; name: string; description: string; option_axes: Record<string, string[]> }[]
	>`SELECT id, slug, name, description, option_axes FROM product_groups
	   WHERE slug = ${params.slug} AND status = 'active'`;
	const group = groups[0];
	if (!group) error(404, 'No such product');

	const rows = await sql<
		{ sku: string; options: Record<string, string>; name: string; price_minor: bigint;
		  tags: string[]; available: number; low: number; hsn_sac: string; gst_rate_bp: number }[]
	>`SELECT i.sku, i.options, i.name, i.price_minor, i.tags, s.available,
	         i.low_stock_threshold AS low, i.hsn_sac, i.gst_rate_bp
	    FROM catalogue_items i JOIN stock s ON s.sku = i.sku
	   WHERE i.group_id = ${group.id} AND i.status = 'active'
	   ORDER BY i.sku`;

	const items = rows.map((r) => ({
		sku: r.sku,
		options: r.options,
		name: r.name,
		price_minor: Number(r.price_minor),
		tags: r.tags,
		// The bucket, never the count. This object is serialized to the browser.
		bucket: bucketFor(r.available, r.low)
	}));

	const addons = await sql<{ sku: string; name: string; price_minor: bigint }[]>`
		SELECT sku, name, price_minor FROM catalogue_items
		 WHERE tags @> '["addon"]'::jsonb AND status = 'active' ORDER BY sku`;

	return {
		group: { ...group, bucket: groupBucket(items.map((i) => i.bucket as Bucket)) },
		items,
		addons: addons.map((a) => ({ ...a, price_minor: Number(a.price_minor) })),
		// The card URL a Consumer pastes into their own agent. This is what
		// replaces the direct cart under cut 5, and it is the surface the demo
		// actually uses.
		cardUrl: `${process.env.OPENSTORE_PUBLIC_ORIGIN ?? 'http://spoiledduckie.localhost'}/.well-known/agent-commerce.json`
	};
}
