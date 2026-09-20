/**
 * The gapless tax invoice number (ADR-0020).
 *
 * Assigned at **dispatch**, not at `paid`: CGST §31(1)(a) ties a goods invoice
 * to removal of the goods, and COD reaches `paid` only when cash is collected
 * at the doorstep — after the goods have already moved. An invoice generated at
 * `paid` would be issued after removal on most Indian e-commerce, which is the
 * one thing Rule 46 forbids.
 *
 * The financial year is **April–March evaluated in `Asia/Kolkata`**. IST is
 * UTC+5:30, so the boundary is 18:30 UTC on 31 March: every dispatch in the
 * first five and a half hours of 1 April IST still carries a 31-March UTC date,
 * and a UTC-derived bucket would hand the new year's first invoices to the year
 * that just closed.
 */
import type { Sql } from 'postgres';

export function financialYear(at: Date): string {
	// Shift into IST, then read the calendar date. No library: the offset is
	// fixed at +5:30 and India has no daylight saving.
	const ist = new Date(at.getTime() + 5.5 * 60 * 60 * 1000);
	const year = ist.getUTCFullYear();
	const month = ist.getUTCMonth() + 1; // 1-indexed
	const start = month >= 4 ? year : year - 1;
	return `${start}-${String((start + 1) % 100).padStart(2, '0')}`;
}

/** Statuses a dispatch may be recorded on: `confirmed` onward, never terminal-negative. */
export const DISPATCHABLE = new Set(['confirmed', 'paid', 'refunded', 'completed']);

export class DispatchError extends Error {
	constructor(readonly code: string, message: string) {
		super(message);
	}
}

/**
 * Assign the next number for this financial year, atomically.
 *
 * `ON CONFLICT DO UPDATE ... RETURNING` is one statement, so two concurrent
 * dispatch calls cannot read the same value and both use it.
 */
export async function assignInvoiceNumber(
	sql: Sql,
	orderId: string,
	at: Date = new Date()
): Promise<string> {
	const rows = await sql<{ status: string; invoice_number: string | null }[]>`
		SELECT status, invoice_number FROM orders WHERE order_id = ${orderId}`;
	const order = rows[0];
	if (!order) throw new DispatchError('not-found', 'no such order');

	// Never re-derived, never reused: a second dispatch call returns the number
	// the first one assigned.
	if (order.invoice_number) return order.invoice_number;

	if (!DISPATCHABLE.has(order.status)) {
		throw new DispatchError(
			'dispatch-not-allowed',
			`an order at ${order.status} has not shipped anything. Goods that never left ` +
				`cannot carry a tax invoice, and a sequence number burned on one is a gap ` +
				`somebody has to explain.`
		);
	}

	const fy = financialYear(at);
	const identity = await sql<{ invoice_prefix: string }[]>`
		SELECT invoice_prefix FROM merchant_tax_identity WHERE id = 1`;
	const prefix = identity[0]?.invoice_prefix ?? 'SD';

	const assigned = await sql<{ next_number: number }[]>`
		INSERT INTO invoice_sequences (financial_year, next_number) VALUES (${fy}, 2)
		ON CONFLICT (financial_year)
		DO UPDATE SET next_number = invoice_sequences.next_number + 1
		RETURNING invoice_sequences.next_number - 1 AS next_number`;
	const sequence = assigned[0]?.next_number ?? 1;
	const number = `${prefix}/${fy}/${String(sequence).padStart(4, '0')}`;

	// Only if nobody else won the race between the SELECT above and here.
	const updated = await sql<{ invoice_number: string }[]>`
		UPDATE orders SET invoice_number = ${number}, dispatched_at = COALESCE(dispatched_at, ${at})
		 WHERE order_id = ${orderId} AND invoice_number IS NULL
		RETURNING invoice_number`;
	if (updated.length) return number;

	const existing = await sql<{ invoice_number: string }[]>`
		SELECT invoice_number FROM orders WHERE order_id = ${orderId}`;
	return existing[0]?.invoice_number ?? number;
}
