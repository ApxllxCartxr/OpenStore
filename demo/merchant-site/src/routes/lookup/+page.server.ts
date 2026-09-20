import { fail } from '@sveltejs/kit';
import { sql } from '$lib/db.ts';
import { timingSafeEqual } from 'node:crypto';

/**
 * Order lookup **by unguessable token only**.
 *
 * A guessable lookup key is an IDOR that hands strangers other people's
 * addresses — which is why the human-readable order number additionally
 * requires a matching Contact Point, under a throttle. The token path needs no
 * second factor because 128 bits already is one.
 */
const attempts = new Map<string, { count: number; first: number }>();
const WINDOW_MS = 60 * 60 * 1000;
const MAX_ATTEMPTS = 5;

function throttle(key: string): boolean {
	const now = Date.now();
	const entry = attempts.get(key);
	if (!entry || now - entry.first > WINDOW_MS) {
		attempts.set(key, { count: 1, first: now });
		return true;
	}
	entry.count += 1;
	return entry.count <= MAX_ATTEMPTS;
}

function matches(a: string, b: string): boolean {
	const x = Buffer.from(a.trim().toLowerCase());
	const y = Buffer.from(b.trim().toLowerCase());
	return x.length === y.length && timingSafeEqual(x, y);
}

export const actions = {
	default: async ({ request, getClientAddress }) => {
		const form = await request.formData();
		const token = String(form.get('token') ?? '').trim();
		const orderNumber = String(form.get('order_number') ?? '').trim();
		const contact = String(form.get('contact') ?? '').trim();

		if (!throttle(getClientAddress())) {
			return fail(429, { message: 'Too many lookups. Try again later.' });
		}

		const rows = token
			? await sql`SELECT order_id, status, total_minor, contact, receipt_id, tracking_number,
			                   carrier, invoice_number
			              FROM orders WHERE receipt_id = ${token}`
			: await sql`SELECT order_id, status, total_minor, contact, receipt_id, tracking_number,
			                   carrier, invoice_number
			              FROM orders WHERE order_id = ${orderNumber}`;

		const order = rows[0];
		// A wrong token and a real order belonging to somebody else answer
		// identically: the difference between them is the oracle this exists to
		// close.
		const notFound = { message: 'No order matches that.' };
		if (!order) return fail(404, notFound);

		if (!token) {
			const stored = (order.contact ?? {}) as Record<string, string>;
			const ok =
				(stored.email && matches(stored.email, contact)) ||
				(stored.phone && matches(stored.phone, contact));
			if (!ok) return fail(404, notFound);
		}

		return {
			order: {
				order_id: order.order_id,
				status: order.status,
				total_minor: Number(order.total_minor),
				receipt_id: order.receipt_id,
				tracking_number: order.tracking_number,
				carrier: order.carrier,
				invoice_number: order.invoice_number
			}
		};
	}
};
