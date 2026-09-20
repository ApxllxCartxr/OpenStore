import { sql } from '$lib/db.ts';

export async function load({ url }) {
	const channel = url.searchParams.get('channel') ?? '';
	const moves = await sql<
		{ at: Date; sku: string; delta: number; channel: string; actor: string; reason: string }[]
	>`SELECT at, sku, delta, channel, actor, reason FROM stock_moves
	   WHERE (${channel} = '' OR channel = ${channel})
	   ORDER BY id DESC LIMIT 200`;
	return {
		channel,
		moves: moves.map((m) => ({ ...m, at: m.at.toISOString() }))
	};
}
