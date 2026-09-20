/**
 * Admin access control, and it comes first.
 *
 * This surface holds the **Refund button**, which moves real money through the
 * sidecar. An unauthenticated admin is a refund endpoint for anyone who can
 * reach the container, and that is the one hole a demo must not ship.
 *
 * Single operator, scrypt (memory-hard, standard library, no native build),
 * session cookie, CSRF on every mutation, and bound to the private network.
 */
import { randomBytes, scryptSync, timingSafeEqual } from 'node:crypto';
import { sql } from './db.ts';

const SESSION_TTL_MS = 12 * 60 * 60 * 1000;
export const SESSION_COOKIE = 'sd_admin';
export const CSRF_COOKIE = 'sd_csrf';

const sessions = new Map<string, { email: string; csrf: string; expires: number }>();

export function hashPassword(password: string): string {
	const salt = randomBytes(16);
	const hash = scryptSync(password, salt, 64, { N: 16384, r: 8, p: 1 });
	return `scrypt$${salt.toString('hex')}$${hash.toString('hex')}`;
}

export function verifyPassword(password: string, stored: string): boolean {
	const [scheme, saltHex, hashHex] = stored.split('$');
	if (scheme !== 'scrypt' || !saltHex || !hashHex) return false;
	const expected = Buffer.from(hashHex, 'hex');
	const actual = scryptSync(password, Buffer.from(saltHex, 'hex'), expected.length, {
		N: 16384,
		r: 8,
		p: 1
	});
	// compare_digest, not ===: a byte-by-byte comparison leaks the correct
	// prefix through timing.
	return expected.length === actual.length && timingSafeEqual(expected, actual);
}

export async function login(email: string, password: string): Promise<{ session: string; csrf: string } | null> {
	const rows = await sql<{ email: string; password_hash: string }[]>`
		SELECT email, password_hash FROM admin_users WHERE email = ${email}`;
	const user = rows[0];
	// Hash anyway when the user is absent, so a missing account and a wrong
	// password take the same time. Otherwise the login form enumerates users.
	const stored = user?.password_hash ?? hashPassword(randomBytes(16).toString('hex'));
	if (!verifyPassword(password, stored) || !user) return null;

	const session = randomBytes(24).toString('base64url');
	const csrf = randomBytes(24).toString('base64url');
	sessions.set(session, { email: user.email, csrf, expires: Date.now() + SESSION_TTL_MS });
	return { session, csrf };
}

export function resolve(session: string | undefined): { email: string; csrf: string } | null {
	if (!session) return null;
	const record = sessions.get(session);
	if (!record) return null;
	if (Date.now() > record.expires) {
		sessions.delete(session);
		return null;
	}
	return { email: record.email, csrf: record.csrf };
}

export function logout(session: string | undefined): void {
	if (session) sessions.delete(session);
}

/**
 * Every mutation checks this. A GET that changes something would bypass it, so
 * there are none — the admin's writes are all POSTs.
 */
export function checkCsrf(session: string | undefined, token: string | undefined): void {
	const record = resolve(session);
	if (!record || !token) throw new Error('csrf: no session');
	const a = Buffer.from(record.csrf);
	const b = Buffer.from(token);
	if (a.length !== b.length || !timingSafeEqual(a, b)) throw new Error('csrf: token mismatch');
}
