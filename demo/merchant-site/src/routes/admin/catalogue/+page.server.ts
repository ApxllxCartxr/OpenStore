import { fail } from '@sveltejs/kit';
import { sql } from '$lib/db.ts';
import { checkCsrf, SESSION_COOKIE } from '$lib/auth.ts';

export async function load() {
	const groups = await sql<{ id: string; name: string; option_axes: Record<string, string[]> }[]>`
		SELECT id, name, option_axes FROM product_groups ORDER BY name`;
	const items = await sql<
		{ sku: string; group_id: string; options: Record<string, string>; name: string;
		  price_minor: bigint; status: string; low_stock_threshold: number; hsn_sac: string;
		  gst_rate_bp: number; available: number }[]
	>`SELECT i.sku, i.group_id, i.options, i.name, i.price_minor, i.status,
	         i.low_stock_threshold, i.hsn_sac, i.gst_rate_bp, s.available
	    FROM catalogue_items i JOIN stock s ON s.sku = i.sku ORDER BY i.group_id, i.sku`;

	/**
	 * The variant matrix: every combination the axes generate, with the ones
	 * that were never made left **absent** rather than zero-stocked. A
	 * zero-stocked row says "we ran out"; an absent one says "this was never a
	 * thing", and a shop that cannot tell them apart reorders the wrong item.
	 */
	const matrix = groups.map((group) => {
		const axes = Object.entries(group.option_axes ?? {});
		const combinations = axes.length
			? axes.reduce<Record<string, string>[]>(
					(acc, [axis, values]) =>
						acc.flatMap((row) => values.map((value) => ({ ...row, [axis]: value }))),
					[{}]
				)
			: [{}];
		return {
			group: group.id,
			name: group.name,
			axes: axes.map(([axis]) => axis),
			rows: combinations.map((combination) => {
				const item = items.find(
					(i) =>
						i.group_id === group.id &&
						axes.every(([axis]) => i.options[axis] === combination[axis])
				);
				return {
					combination,
					item: item
						? {
								sku: item.sku,
								price_minor: Number(item.price_minor),
								available: item.available,
								low: item.low_stock_threshold,
								hsn_sac: item.hsn_sac,
								gst_rate_bp: item.gst_rate_bp,
								status: item.status
							}
						: null
				};
			})
		};
	});

	return { matrix };
}

export const actions = {
	/** Set price and stock. GST and zones come from the seed (cut 4a). */
	update: async ({ request, cookies }) => {
		const form = await request.formData();
		try {
			checkCsrf(cookies.get(SESSION_COOKIE), String(form.get('csrf') ?? ''));
		} catch {
			return fail(403, { message: 'Session expired. Sign in again.' });
		}

		const sku = String(form.get('sku') ?? '');
		const price = Number(form.get('price_minor'));
		const available = Number(form.get('available'));

		// Integers only, negative refused, unknown SKU refused — all named.
		if (!Number.isInteger(price) || price < 0) {
			return fail(400, { message: `Price is paise, an integer >= 0. Got ${form.get('price_minor')}.` });
		}
		if (!Number.isInteger(available) || available < 0) {
			return fail(400, { message: `Stock is an integer >= 0. Got ${form.get('available')}.` });
		}
		const known = await sql`SELECT 1 FROM catalogue_items WHERE sku = ${sku}`;
		if (!known.length) return fail(404, { message: `No Catalogue Item ${sku}.` });

		const current = await sql<{ available: number }[]>`SELECT available FROM stock WHERE sku = ${sku}`;
		const delta = available - (current[0]?.available ?? 0);

		await sql.begin(async (tx) => {
			await tx`UPDATE catalogue_items SET price_minor = ${price} WHERE sku = ${sku}`;
			await tx`UPDATE stock SET available = ${available} WHERE sku = ${sku}`;
			if (delta !== 0) {
				// Every stock move is audited: who, when, delta, channel, reason.
				await tx`INSERT INTO stock_moves (sku, delta, channel, actor, reason)
				         VALUES (${sku}, ${delta}, 'admin-adjust', 'operator', ${String(form.get('reason') ?? 'admin edit')})`;
			}
		});
		return { message: `${sku} updated.` };
	}
};
