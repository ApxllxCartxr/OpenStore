import { describe, expect, it } from 'vitest';
import { bucketFor, groupBucket } from '../src/lib/availability.ts';

describe('the exposure boundary', () => {
	it('cuts at the item’s own threshold', () => {
		expect(bucketFor(0, 3)).toBe('sold-out');
		expect(bucketFor(3, 3)).toBe('low-stock');
		expect(bucketFor(4, 3)).toBe('in-stock');
	});

	it('reads a group in-stock when any item is', () => {
		// Red in stock and black sold out is an in-stock group whose black
		// variant refuses at the door — correct, and why stock is not evaluated
		// at the group.
		expect(groupBucket(['sold-out', 'in-stock'])).toBe('in-stock');
		expect(groupBucket(['sold-out', 'low-stock'])).toBe('low-stock');
		expect(groupBucket(['sold-out'])).toBe('sold-out');
	});

	it('fails loud on a count that should have been rejected at load', () => {
		expect(() => bucketFor(-1, 3)).toThrow(/int >= 0/);
		expect(() => bucketFor(1.5, 3)).toThrow();
	});
});
