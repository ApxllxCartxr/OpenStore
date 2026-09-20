/**
 * ADR-0020's two properties: the financial year is local, and the number is
 * gapless and assigned once.
 */
import { describe, expect, it } from 'vitest';
import { DISPATCHABLE, financialYear } from '../src/lib/invoice.ts';

describe('the financial year is evaluated in Asia/Kolkata', () => {
	it('puts late 31 March in the closing year', () => {
		// 23:45 IST on 31 March = 18:15 UTC the same day.
		expect(financialYear(new Date('2026-03-31T18:15:00Z'))).toBe('2025-26');
	});

	it('puts early 1 April IST in the NEW year, though UTC still says 31 March', () => {
		// 00:15 IST on 1 April = 18:45 UTC on 31 March. This is the case a
		// UTC-derived bucket gets wrong, every year, in one direction.
		expect(financialYear(new Date('2026-03-31T18:45:00Z'))).toBe('2026-27');
	});

	it('handles the whole five-and-a-half-hour window', () => {
		// 18:30 UTC on 31 March is exactly midnight IST on 1 April.
		expect(financialYear(new Date('2026-03-31T18:29:59Z'))).toBe('2025-26');
		expect(financialYear(new Date('2026-03-31T18:30:00Z'))).toBe('2026-27');
		expect(financialYear(new Date('2026-03-31T23:59:59Z'))).toBe('2026-27');
	});

	it('labels the year the way an Indian invoice does', () => {
		expect(financialYear(new Date('2026-09-21T06:00:00Z'))).toBe('2026-27');
		expect(financialYear(new Date('2026-01-15T06:00:00Z'))).toBe('2025-26');
	});
});

describe('which statuses may dispatch', () => {
	it('is confirmed onward and never terminal-negative', () => {
		expect([...DISPATCHABLE].sort()).toEqual(['completed', 'confirmed', 'paid', 'refunded']);
		for (const status of ['pending', 'cancelled', 'expired', 'failed']) {
			expect(DISPATCHABLE.has(status)).toBe(false);
		}
	});

	it('allows a COD order to dispatch while still confirmed, before paid exists', () => {
		expect(DISPATCHABLE.has('confirmed')).toBe(true);
	});
});
