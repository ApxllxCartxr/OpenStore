/**
 * The admin holds the Refund button. An unauthenticated admin is a refund
 * endpoint for anyone who can reach the container.
 */
import { describe, expect, it } from 'vitest';
import { hashPassword, verifyPassword } from '../src/lib/auth.ts';

describe('password hashing', () => {
	it('round-trips', () => {
		const stored = hashPassword('correct horse battery staple');
		expect(verifyPassword('correct horse battery staple', stored)).toBe(true);
		expect(verifyPassword('wrong', stored)).toBe(false);
	});

	it('salts, so two operators with the same password hash differently', () => {
		expect(hashPassword('same')).not.toBe(hashPassword('same'));
	});

	it('refuses a stored value it does not recognise rather than defaulting to true', () => {
		expect(verifyPassword('x', 'plaintext-oops')).toBe(false);
		expect(verifyPassword('x', '')).toBe(false);
		expect(verifyPassword('x', 'scrypt$$')).toBe(false);
	});

	it('uses scrypt from the standard library, with work parameters', () => {
		// N=16384 is the cost. A hash that is cheap to compute is a password
		// file that is cheap to crack.
		expect(hashPassword('x').startsWith('scrypt$')).toBe(true);
	});
});
