import { sql } from '$lib/db.ts';

/** Orders that represent money actually taken. `pending` and `confirmed` are a
 *  basket and a hold, not a sale, and counting them would let an abandoned
 *  checkout inflate the day's revenue. */
const SOLD = ['paid', 'completed', 'refunded'];

export async function load() {
	const [open, refundRequested, lowStock, failed, daily, channel] = await Promise.all([
		sql<{ n: bigint }[]>`SELECT count(*) AS n FROM orders WHERE status IN ('pending','confirmed')`,
		sql<{ n: bigint }[]>`SELECT count(*) AS n FROM orders WHERE cancel_reason = 'refund-requested'`,
		sql<{ sku: string; available: number; low: number }[]>`
			SELECT s.sku, s.available, i.low_stock_threshold AS low
			  FROM stock s JOIN catalogue_items i ON i.sku = s.sku
			 WHERE s.available <= i.low_stock_threshold ORDER BY s.available, s.sku`,
		sql<{ n: bigint }[]>`SELECT count(*) AS n FROM orders WHERE status = 'failed'`,
		// Grouped in SQL rather than in the page: the page should not be the
		// place that decides what a day is.
		sql<{ day: Date; total: bigint }[]>`
			SELECT date_trunc('day', created_at) AS day, sum(total_minor) AS total
			  FROM orders
			 WHERE status = ANY(${SOLD}) AND created_at >= now() - interval '13 days'
			 GROUP BY 1 ORDER BY 1`,
		// An agent-originated order carries the agent's id; a human checking out
		// on the site leaves it empty. That one column is the whole split.
		sql<{ agent: boolean; n: bigint; total: bigint }[]>`
			SELECT agent_id <> '' AS agent, count(*) AS n, sum(total_minor) AS total
			  FROM orders WHERE status = ANY(${SOLD}) GROUP BY 1`
	]);

	// Empty days keep their slot. Dropping them would compress the axis and turn
	// a quiet week into a busy one.
	const byDay = new Map(daily.map((r) => [r.day.toISOString().slice(0, 10), Number(r.total)]));
	const today = new Date();
	const series = Array.from({ length: 14 }, (_, i) => {
		const d = new Date(today);
		d.setDate(d.getDate() - (13 - i));
		const key = d.toISOString().slice(0, 10);
		return {
			label: d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short' }),
			value: byDay.get(key) ?? 0
		};
	});

	const agent = channel.find((r) => r.agent);
	const direct = channel.find((r) => !r.agent);
	const agentOrders = Number(agent?.n ?? 0);
	const directOrders = Number(direct?.n ?? 0);

	return {
		open: Number(open[0]?.n ?? 0),
		refundRequested: Number(refundRequested[0]?.n ?? 0),
		failed: Number(failed[0]?.n ?? 0),
		lowStock,
		series,
		revenue: Number(agent?.total ?? 0) + Number(direct?.total ?? 0),
		agentOrders,
		directOrders
	};
}
