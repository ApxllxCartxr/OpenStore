/**
 * Calls **out** to the sidecar, over HMAC.
 *
 * Refund, shop-reject, COD collection and RTO all go through here and **never**
 * write the order row directly. The sidecar holds the `RESERVE`, so a local
 * write strands a Ledger hold — and routing through it serialises shop-reject
 * against a Consumer tap and the expiry sweep on one `set-status` key.
 *
 * This is the only direction the merchant site talks to the sidecar, and it is
 * HTTP. There is no import, in either direction.
 */
import { randomBytes } from 'node:crypto';
import { sign } from './hmac.ts';

const BASE = process.env.SIDECAR_INTERNAL_URL ?? 'http://sidecar:8000';
const SECRET = process.env.TRAIT_HMAC_SECRET ?? 'conformance-secret';

export class SidecarError extends Error {
	constructor(
		readonly code: string,
		message: string
	) {
		super(message);
	}
}

export async function callSidecar(
	path: string,
	payload: Record<string, unknown>,
	idempotencyKey: string
): Promise<Record<string, unknown>> {
	const body = JSON.stringify(payload);
	const timestamp = Math.floor(Date.now() / 1000);
	const nonce = randomBytes(16).toString('hex');

	const response = await fetch(`${BASE}${path}`, {
		method: 'POST',
		headers: {
			'content-type': 'application/json',
			'x-openstore-timestamp': String(timestamp),
			'x-openstore-nonce': nonce,
			'x-openstore-signature': sign(SECRET, { body, timestamp, nonce, path }),
			'idempotency-key': idempotencyKey
		},
		body
	});

	const parsed = (await response.json().catch(() => null)) as
		| { error?: { code: string; detail: string } }
		| Record<string, unknown>
		| null;

	if (!response.ok) {
		const error = (parsed as { error?: { code: string; detail: string } })?.error;
		// Never invent a code: a refusal we cannot parse is a protocol failure,
		// not a business outcome, and treating it as one would put a fabricated
		// reason in front of an operator.
		if (!error) {
			throw new SidecarError(
				'protocol',
				`sidecar answered HTTP ${response.status} with no error envelope`
			);
		}
		throw new SidecarError(error.code, error.detail);
	}
	return (parsed ?? {}) as Record<string, unknown>;
}
