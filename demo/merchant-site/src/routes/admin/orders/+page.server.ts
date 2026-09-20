import { sql } from '$lib/db.ts';

export async function load({ url }) {
	const status = url.searchParams.get('status') ?? '';
	const query = (url.searchParams.get('q') ?? '').trim();
	const orders = await sql<
		{ order_id: string; status: string; total_minor: bigint; refunded_minor: bigint;
		  invoice_number: string | null; payment_method: string; created_at: Date }[]
	>`SELECT order_id, status, total_minor, refunded_minor, invoice_number, payment_method, created_at
	    FROM orders
	   WHERE (${status} = '' OR status = ${status})
	     AND (${query} = '' OR order_id ILIKE ${'%' + query + '%'})
	   ORDER BY created_at DESC LIMIT 100`;
	return {
		status,
		query,
		orders: orders.map((o) => ({
			...o,
			total_minor: Number(o.total_minor),
			refunded_minor: Number(o.refunded_minor),
			created_at: o.created_at.toISOString()
		}))
	};
}
