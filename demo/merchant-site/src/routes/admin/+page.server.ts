import { sql } from '$lib/db.ts';

export async function load() {
	const [open, refundRequested, lowStock, failed] = await Promise.all([
		sql<{ n: bigint }[]>`SELECT count(*) AS n FROM orders WHERE status IN ('pending','confirmed')`,
		sql<{ n: bigint }[]>`SELECT count(*) AS n FROM orders WHERE cancel_reason = 'refund-requested'`,
		sql<{ sku: string; available: number; low: number }[]>`
			SELECT s.sku, s.available, i.low_stock_threshold AS low
			  FROM stock s JOIN catalogue_items i ON i.sku = s.sku
			 WHERE s.available <= i.low_stock_threshold ORDER BY s.available, s.sku`,
		sql<{ n: bigint }[]>`SELECT count(*) AS n FROM orders WHERE status = 'failed'`
	]);
	return {
		open: Number(open[0]?.n ?? 0),
		refundRequested: Number(refundRequested[0]?.n ?? 0),
		failed: Number(failed[0]?.n ?? 0),
		lowStock
	};
}
