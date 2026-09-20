/**
 * C5 — the smoothness law as tests. Inconvenient is a red build.
 */
import { describe, expect, it } from 'vitest';
import {
	assertResumedState,
	assertSmooth,
	SmoothnessViolation,
	type Event
} from '../src/lib/smoothness.ts';

const HAPPY: Event[] = [
	{ kind: 'permission', tool: 'search', decision: 'always' },
	{ kind: 'tool', name: 'search' },
	{ kind: 'tool', name: 'read-item' },
	{ kind: 'tool', name: 'add-line' },
	{ kind: 'permission', tool: 'place-order', decision: 'allow-once' },
	{ kind: 'tap', order_id: 'ord_1' },
	{ kind: 'resume', order_id: 'ord_1', thread_id: 'thread_1' },
	{ kind: 'tool', name: 'order-status' }
];

describe('the happy path is smooth', () => {
	it('passes every gate', () => {
		expect(() => assertSmooth(HAPPY)).not.toThrow();
	});
});

describe('context loss is a red build', () => {
	it('catches a second tap for one order', () => {
		expect(() => assertSmooth([...HAPPY, { kind: 'tap', order_id: 'ord_1' }])).toThrow(
			SmoothnessViolation
		);
	});

	it('catches a re-search after the tap', () => {
		expect(() => assertSmooth([...HAPPY, { kind: 'tool', name: 'search' }])).toThrow(
			/searched again after the tap/
		);
	});

	it('catches a second sign-in', () => {
		expect(() =>
			assertSmooth([{ kind: 'login' }, ...HAPPY, { kind: 'login' }])
		).toThrow(/sign in twice/);
	});

	it('catches extra Allow-once prompts for one spend', () => {
		const noisy: Event[] = [
			{ kind: 'permission', tool: 'add-line', decision: 'allow-once' },
			{ kind: 'permission', tool: 'start-checkout', decision: 'allow-once' },
			{ kind: 'permission', tool: 'place-order', decision: 'allow-once' },
			{ kind: 'tap', order_id: 'ord_1' }
		];
		expect(() => assertSmooth(noisy)).toThrow(/Allow-once prompts/);
	});
});

describe('resumed state is exact on both sides', () => {
	const before = { order_id: 'ord_1', thread_id: 'thread_1', lines: ['SD-TOTE-BLK-M', 'SD-GIFTWRAP'] };

	it('accepts an identical restore', () => {
		expect(() => assertResumedState(before, { ...before })).not.toThrow();
	});

	it('catches resuming into another order', () => {
		expect(() => assertResumedState(before, { ...before, order_id: 'ord_2' })).toThrow(
			/different order or thread/
		);
	});

	it('catches a basket that changed across the approve page', () => {
		expect(() =>
			assertResumedState(before, { ...before, lines: ['SD-TOTE-BLK-M'] })
		).toThrow(/basket changed/);
	});
});
