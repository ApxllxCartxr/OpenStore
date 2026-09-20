import { error, fail } from '@sveltejs/kit';
import { sql } from '$lib/db.ts';
import { checkCsrf, SESSION_COOKIE } from '$lib/auth.ts';
import { assignInvoiceNumber, DispatchError } from '$lib/invoice.ts';
import { callSidecar, SidecarError } from '$lib/sidecar.ts';
import { notify } from '$lib/notify.ts';

export async function load({ params }) {
	const rows = await sql`SELECT * FROM orders WHERE order_id = ${params.id}`;
	const order = rows[0];
	if (!order) error(404, 'No such order');

	const moves = await sql<{ at: Date; sku: string; delta: number; channel: string; reason: string }[]>`
		SELECT at, sku, delta, channel, reason FROM stock_moves
		 WHERE reason = ${params.id} ORDER BY id`;
	const refunds = await sql<{ amount_minor: bigint; reason: string; created_at: Date }[]>`
		SELECT amount_minor, reason, created_at FROM refunds
		 WHERE order_id = ${params.id} ORDER BY id`;
	const notes = await sql<{ at: Date; kind: string }[]>`
		SELECT at, kind FROM notifications WHERE order_id = ${params.id} ORDER BY id`;

	return {
		order: {
			order_id: order.order_id,
			status: order.status,
			payment_method: order.payment_method,
			total_minor: Number(order.total_minor),
			refunded_minor: Number(order.refunded_minor),
			lines: order.lines,
			quote: order.quote,
			invoice_number: order.invoice_number,
			tracking_number: order.tracking_number,
			carrier: order.carrier,
			dispatched_at: order.dispatched_at?.toISOString() ?? null,
			collected_at: order.collected_at?.toISOString() ?? null,
			cancel_reason: order.cancel_reason,
			receipt_id: order.receipt_id,
			// The Transcript itself lives sidecar-side; the Timeline shows the
			// reference so an operator can find it there.
			cart_hash: order.cart_hash
		},
		// Timeline: status history + Transcript ref + stock moves + receipt link
		// + invoice number, in one place.
		moves: moves.map((m) => ({ ...m, at: m.at.toISOString() })),
		refunds: refunds.map((r) => ({
			amount_minor: Number(r.amount_minor),
			reason: r.reason,
			created_at: r.created_at.toISOString()
		})),
		notifications: notes.map((n) => ({ kind: n.kind, at: n.at.toISOString() }))
	};
}

/**
 * **Every action here calls the sidecar over HMAC and never writes the order
 * row directly**, except the dispatch record — which is Merchant-owned data
 * (tracking, carrier, invoice number) that the sidecar computes none of.
 *
 * The sidecar holds the `RESERVE`, so a local status write strands a Ledger
 * hold; and routing through door 8 serialises shop-reject against a Consumer
 * tap and the expiry sweep on one key.
 */
export const actions = {
	dispatch: async ({ request, params, cookies }) => {
		const form = await request.formData();
		try {
			checkCsrf(cookies.get(SESSION_COOKIE), String(form.get('csrf') ?? ''));
		} catch {
			return fail(403, { message: 'Session expired. Sign in again.' });
		}

		const tracking = String(form.get('tracking_number') ?? '').trim();
		const carrier = String(form.get('carrier') ?? '').trim();

		try {
			// Assigns the gapless invoice number on the FIRST dispatch only, and
			// refuses on a terminal-negative status.
			const invoice = await assignInvoiceNumber(sql, params.id);
			if (tracking || carrier) {
				await sql`UPDATE orders SET tracking_number = COALESCE(NULLIF(${tracking}, ''), tracking_number),
				                            carrier = COALESCE(NULLIF(${carrier}, ''), carrier)
				           WHERE order_id = ${params.id}`;
			}
			await notify(params.id, 'shipped', {
				invoice_number: invoice,
				tracking_number: tracking || null,
				carrier: carrier || null
			});
			return { message: `Dispatched. Invoice ${invoice}.` };
		} catch (err) {
			if (err instanceof DispatchError) return fail(409, { message: err.message });
			throw err;
		}
	},

	refund: async ({ request, params, cookies }) => {
		const form = await request.formData();
		try {
			checkCsrf(cookies.get(SESSION_COOKIE), String(form.get('csrf') ?? ''));
		} catch {
			return fail(403, { message: 'Session expired. Sign in again.' });
		}

		const amount = Number(form.get('amount_minor'));
		const reason = String(form.get('reason') ?? '');
		const restock = form.getAll('restock').map(String);

		try {
			await callSidecar(
				'/agentic/refund',
				{ order_id: params.id, amount_minor: amount, reason, restock_lines: restock },
				`${params.id}:refund:${Date.now()}`
			);
		} catch (err) {
			if (err instanceof SidecarError) return fail(409, { message: `${err.code}: ${err.message}` });
			throw err;
		}
		return { message: 'Refund sent to the sidecar.' };
	},

	collect: async ({ request, params, cookies }) => {
		/** COD only: the cash is in hand, and this is where `paid` comes from.
		 *  The sidecar writes the CAPTURE — with no preceding RESERVE, because no
		 *  money was ever held across the delivery (ADR-0018). */
		const form = await request.formData();
		try {
			checkCsrf(cookies.get(SESSION_COOKIE), String(form.get('csrf') ?? ''));
		} catch {
			return fail(403, { message: 'Session expired. Sign in again.' });
		}
		try {
			await callSidecar('/agentic/collect', { order_id: params.id }, `${params.id}:collect:1`);
			await sql`UPDATE orders SET collected_at = now() WHERE order_id = ${params.id}`;
		} catch (err) {
			if (err instanceof SidecarError) return fail(409, { message: `${err.code}: ${err.message}` });
			throw err;
		}
		return { message: 'Collection recorded.' };
	},

	rto: async ({ request, params, cookies }) => {
		/** Returned to origin. `cancelled` with reason `rto` and a restock —
		 *  already what `cancelled` means: pre-money, stock returned. **No Ledger
		 *  entry of any kind**, because nothing moved. */
		const form = await request.formData();
		try {
			checkCsrf(cookies.get(SESSION_COOKIE), String(form.get('csrf') ?? ''));
		} catch {
			return fail(403, { message: 'Session expired. Sign in again.' });
		}
		try {
			await callSidecar('/agentic/rto', { order_id: params.id }, `${params.id}:rto:1`);
		} catch (err) {
			if (err instanceof SidecarError) return fail(409, { message: `${err.code}: ${err.message}` });
			throw err;
		}
		return { message: 'RTO recorded: cancelled, stock returned, no money moved.' };
	},

	reject: async ({ request, params, cookies }) => {
		const form = await request.formData();
		try {
			checkCsrf(cookies.get(SESSION_COOKIE), String(form.get('csrf') ?? ''));
		} catch {
			return fail(403, { message: 'Session expired. Sign in again.' });
		}
		try {
			await callSidecar(
				'/agentic/reject',
				{ order_id: params.id, reason: String(form.get('reason') ?? 'shop-reject') },
				`${params.id}:reject:1`
			);
		} catch (err) {
			if (err instanceof SidecarError) return fail(409, { message: `${err.code}: ${err.message}` });
			throw err;
		}
		return { message: 'Order rejected.' };
	}
};
