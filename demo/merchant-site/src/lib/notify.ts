/**
 * Consumer and Merchant notifications, owned here.
 *
 * This side owns customer comms and the only Contact Point plaintext (SPEC
 * §10), so the sidecar sends no email and no SMS and never receives pushed PII.
 *
 * Pluggable sender with a log-only default, so the demo needs no external
 * account. Templates render the **Quote breakdown verbatim**, never a
 * recomputed total: a notification that recalculates is a second money path
 * with a friendly tone.
 */
import { sql } from './db.ts';

export type NotificationKind =
	| 'order-received'   // on `confirmed`: awaiting payment, NOT "confirmed"
	| 'order-confirmed'  // on `paid`, with the receipt link
	| 'shipped'          // at dispatch, whenever that is
	| 'refunded'
	| 'expired'
	| 'new-order';       // to the Merchant

/**
 * Idempotent by construction: one row per (order, kind). A retry writes
 * nothing, which is what "each status change fires exactly one notification"
 * actually requires — and dispatch is **not** a status change, so it needs this
 * guard rather than inheriting one.
 */
/** What a notification body may contain: JSON, and nothing that serializes
 *  differently than it reads. */
export type NotifyBody = Record<string, string | number | boolean | null>;

export async function notify(
	orderId: string | null,
	kind: NotificationKind,
	body: NotifyBody
): Promise<boolean> {
	if (orderId) {
		const existing = await sql`SELECT 1 FROM notifications WHERE order_id = ${orderId} AND kind = ${kind}`;
		if (existing.length) return false;

		const rows = await sql<{ contact: Record<string, string> | null; erased_at: Date | null }[]>`
			SELECT contact, erased_at FROM orders WHERE order_id = ${orderId}`;
		const order = rows[0];
		if (order?.erased_at || !order?.contact) {
			// An erased Contact Point skips the send with a named reason rather
			// than throwing. Erasure must never break the system that honoured it.
			await sql`INSERT INTO notifications (order_id, kind, channel, body)
			          VALUES (${orderId}, ${kind}, 'skipped',
			                  ${sql.json({ reason: 'contact-erased' })})`;
			return false;
		}
	}

	await sql`INSERT INTO notifications (order_id, kind, channel, body)
	          VALUES (${orderId}, ${kind}, ${process.env.NOTIFY_CHANNEL ?? 'log'}, ${sql.json(body)})`;
	console.log(JSON.stringify({ event: 'notification', order_id: orderId, kind }));
	return true;
}
