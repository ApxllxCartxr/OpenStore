/**
 * C3's core claims: the agent computes nothing, standing approval never covers
 * a spend, and every refusal has copy that names the fix.
 */
import { describe, expect, it } from 'vitest';
import {
	assertVerbatim,
	displayTotal,
	explain,
	formatRupees,
	QuoteError,
	REASON_COPY,
	renderRows,
	type SignedQuote
} from '../src/lib/quote.ts';
import {
	ALWAYS_ALLOWABLE,
	assertAddonHasParent,
	assertResolvedVariant,
	boundedSteps,
	MAX_STEPS,
	permissionRequest,
	requiresFreshConsent,
	SCOPES,
	ToolError,
	TOOLS,
	validate
} from '../src/lib/tools/loop.ts';
import { MalformedPlan, parseCalls, SCRIPTED_SEQUENCE, ScriptedDriver } from '../src/lib/model/driver.ts';

/** The §16.11 worked example, as the Merchant signs it. */
const QUOTE: SignedQuote = {
	currency: 'INR',
	subtotal_minor: 249800,
	lines: [
		{ sku: 'SD-CHARMBAR-SEAT', qty: 1, unit_price_minor: 150000, line_total_minor: 150000, addons: [] },
		{ sku: 'SD-TOTE-BLK-M', qty: 1, unit_price_minor: 89900, line_total_minor: 99800,
		  addons: [{ sku: 'SD-GIFTWRAP', amount_minor: 9900 }] }
	],
	discount_lines: [],
	fulfillment_chosen: { id: 'rest-of-india', cost_minor: 9900 },
	fulfillment_options: [{ id: 'rest-of-india', label: 'Rest of India', cost_minor: 9900, eta_days: 5 }],
	tax_lines: [
		{ kind: 'CGST', label: 'CGST 9%', amount_minor: 11894, informational: true },
		{ kind: 'IGST', label: 'IGST 18%', amount_minor: 15827, informational: true },
		{ kind: 'SGST', label: 'SGST 9%', amount_minor: 11894, informational: true }
	],
	round_off_minor: 0,
	total_minor: 259700,
	tax_inclusive: true
};

describe('the rendered breakdown is verbatim', () => {
	it('shows only figures the Merchant signed', () => {
		const rows = renderRows(QUOTE);
		expect(() => assertVerbatim(QUOTE, rows)).not.toThrow();
	});

	it('takes the total from the Quote rather than summing the rows', () => {
		// The agent has no total of its own. If it summed, a Merchant whose own
		// sums were wrong would be silently corrected — and the Consumer would
		// approve a number nobody signed.
		expect(displayTotal(QUOTE)).toBe(259700);
	});

	it('refuses a row carrying a figure that is not in the Quote', () => {
		const rows = [...renderRows(QUOTE), { label: 'Handling', amount_minor: 4999 }];
		expect(() => assertVerbatim(QUOTE, rows)).toThrow(QuoteError);
		expect(() => assertVerbatim(QUOTE, rows)).toThrow(/does not compute amounts/);
	});

	it('marks an inclusive tax as included rather than adding it', () => {
		const tax = renderRows(QUOTE).find((r) => r.label === 'IGST 18%');
		expect(tax?.note).toBe('included');
	});

	it('shows the ETA as the day count the Merchant sent', () => {
		expect(renderRows(QUOTE).find((r) => r.label === 'Rest of India')?.note).toBe('5 days');
	});

	it('names the add-on inside its parent line rather than as its own row', () => {
		const rows = renderRows(QUOTE);
		expect(rows.filter((r) => r.label.includes('GIFTWRAP'))).toHaveLength(0);
		expect(rows.find((r) => r.label === 'SD-TOTE-BLK-M')?.note).toMatch(/SD-GIFTWRAP/);
	});

	it('formats money by integer arithmetic', () => {
		expect(formatRupees(259700)).toBe('₹2,597.00');
		expect(formatRupees(-10000)).toBe('-₹100.00');
	});
});

describe('permissions', () => {
	it('never lets standing approval cover a spend step', () => {
		const standing = new Set(TOOLS); // the Consumer said "always" to everything
		expect(requiresFreshConsent('place-order', standing)).toBe(true);
		expect(requiresFreshConsent('start-checkout', standing)).toBe(true);
		expect(requiresFreshConsent('add-line', standing)).toBe(true);
		// Reads and drafts only.
		expect(requiresFreshConsent('search', new Set(['search']))).toBe(false);
	});

	it('only allows standing approval on reads', () => {
		expect([...ALWAYS_ALLOWABLE].sort()).toEqual(['order-status', 'read-item', 'search']);
	});

	it('shows the exact request JSON, not a summary', () => {
		const request = permissionRequest({ name: 'add-line', args: { sku: 'SD-TOTE-BLK-M', qty: 2 } });
		expect(request.request).toEqual({ sku: 'SD-TOTE-BLK-M', qty: 2 });
		expect(request.scope).toBe('build-basket');
	});

	it('marks the spend steps, whose copy grants a scope and never an amount', () => {
		expect(permissionRequest({ name: 'start-checkout', args: {} }).spend).toBe(true);
		expect(permissionRequest({ name: 'place-order', args: {} }).spend).toBe(true);
		expect(permissionRequest({ name: 'search', args: {} }).spend).toBe(false);
	});

	it('maps every tool to a scope', () => {
		expect(Object.keys(SCOPES).sort()).toEqual([...TOOLS].sort());
	});
});

describe('the chat never picks for the Consumer', () => {
	it('refuses a group id where a SKU belongs', () => {
		expect(() => assertResolvedVariant('tote', new Set(['tote']))).toThrow(ToolError);
		expect(() => assertResolvedVariant('tote', new Set(['tote']))).toThrow(/Choose the options/);
	});

	it('refuses an add with no resolved SKU', () => {
		expect(() => validate({ name: 'add-line', args: {} })).toThrow(/Pick the exact option/);
	});

	it('refuses an unparented add-on at the moment it is added', () => {
		const addons = new Set(['SD-GIFTWRAP']);
		expect(() => assertAddonHasParent('SD-GIFTWRAP', undefined, addons, new Set())).toThrow(
			/Add the item it goes with/
		);
		expect(() =>
			assertAddonHasParent('SD-GIFTWRAP', 'SD-TOTE-BLK-M', addons, new Set(['SD-TOTE-BLK-M']))
		).not.toThrow();
	});
});

describe('malformed model output changes nothing', () => {
	it('refuses a tool outside the closed set', () => {
		expect(() => parseCalls('{"calls":[{"name":"wire-transfer"}]}', [...TOOLS])).toThrow(MalformedPlan);
	});

	it('refuses output that is not JSON', () => {
		expect(() => parseCalls('I think you should buy the tote!', [...TOOLS])).toThrow(/did not return JSON/);
	});

	it('refuses args that are not an object', () => {
		expect(() => parseCalls('{"calls":[{"name":"search","args":"tote"}]}', [...TOOLS])).toThrow(
			/args must be an object/
		);
	});

	it('bounds the number of steps', () => {
		expect(() => boundedSteps(MAX_STEPS + 1)).toThrow(/limit/);
	});
});

describe('the scripted driver', () => {
	it('emits §16.9’s pinned sequence, one call at a time', async () => {
		const driver = new ScriptedDriver();
		const emitted = [];
		for (let i = 0; i < SCRIPTED_SEQUENCE.length; i += 1) {
			emitted.push(...(await driver.plan()));
		}
		expect(emitted.map((c) => c.name)).toEqual(SCRIPTED_SEQUENCE.map((c) => c.name));
		expect(await driver.plan()).toEqual([]);
	});

	it('is the default, so the demo runs with no model at all', () => {
		expect(new ScriptedDriver().name).toBe('scripted');
	});
});

describe('refusal copy', () => {
	it('names the exact fix for every reason code', () => {
		for (const [code, copy] of Object.entries(REASON_COPY)) {
			expect(copy.length, code).toBeGreaterThan(10);
		}
	});

	it('says nothing about WHY a code failed', () => {
		// A chat that explains is a code oracle with a friendly face.
		expect(REASON_COPY['code-invalid']).toBe('That code did not apply.');
		expect(REASON_COPY['code-invalid']).not.toMatch(/expired|used|unknown|exists/i);
	});

	it('gives `expired` two faces, because it has two', () => {
		expect(explain('time-limit-reached')).toMatch(/nothing was held/i);
		expect(explain('payment-window-elapsed')).toMatch(/hold was released/i);
	});

	it('falls back rather than inventing copy for an unknown code', () => {
		expect(explain('brand-new-code')).toMatch(/brand-new-code/);
	});
});
