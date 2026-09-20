/**
 * The trait's HMAC, verifier side. Mirrors the sidecar's `trait/signing.py`
 * exactly — same preimage, same window, same nonce discipline.
 *
 * **The signature covers the path as well as the body.** `release` and `commit`
 * both carry `{order_id}` and nothing else, so a body-only signature makes a
 * signed release a signed commit, and an attacker who can redirect a request
 * gets a free close of somebody's hold.
 */
import { createHmac, timingSafeEqual } from 'node:crypto';

export const SIGNATURE_HEADER = 'x-openstore-signature';
export const TIMESTAMP_HEADER = 'x-openstore-timestamp';
export const NONCE_HEADER = 'x-openstore-nonce';
export const IDEMPOTENCY_HEADER = 'idempotency-key';

export const REPLAY_WINDOW_SECONDS = 60;

export function sign(
	secret: string,
	{ body, timestamp, nonce, path }: { body: string; timestamp: number; nonce: string; path: string }
): string {
	const preimage = [path, String(timestamp), nonce, body].join('\n');
	return createHmac('sha256', secret).update(preimage).digest('hex');
}

const seen = new Map<string, number>();

export class SignatureError extends Error {}

export function verify(
	secret: string,
	{
		body,
		path,
		signature,
		timestamp,
		nonce,
		now = Math.floor(Date.now() / 1000)
	}: {
		body: string;
		path: string;
		signature: string;
		timestamp: string;
		nonce: string;
		now?: number;
	}
): void {
	const ts = Number.parseInt(timestamp, 10);
	if (!Number.isFinite(ts)) throw new SignatureError(`unparseable timestamp ${timestamp}`);
	if (Math.abs(now - ts) > REPLAY_WINDOW_SECONDS) {
		throw new SignatureError(`timestamp outside the ${REPLAY_WINDOW_SECONDS}s replay window`);
	}

	const expected = sign(secret, { body, timestamp: ts, nonce, path });
	const a = Buffer.from(expected, 'utf8');
	const b = Buffer.from(signature ?? '', 'utf8');
	// Length-checked first: timingSafeEqual throws on a mismatch, which would
	// itself be a timing signal.
	if (a.length !== b.length || !timingSafeEqual(a, b)) {
		throw new SignatureError('signature does not verify');
	}

	// Remembered for exactly the window it is valid in. An unbounded set is a
	// memory leak whose size an attacker chooses.
	for (const [key, at] of seen) if (Math.abs(now - at) > REPLAY_WINDOW_SECONDS) seen.delete(key);
	if (seen.has(nonce)) throw new SignatureError(`nonce ${nonce} has already been used`);
	seen.set(nonce, ts);
}
