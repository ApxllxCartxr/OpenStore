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
	READS,
	assertAddonHasParent,
	assertDestinationFromConsumer,
	assertResolvedVariant,
	boundedSteps,
	catalogueFrom,
	MAX_STEPS,
	consentNote,
	hasMeaningfulArgs,
	permissionRequest,
	requiresFreshConsent,
	SCOPES,
	shopsFor,
	SYSTEM_PROMPT,
	ToolError,
	TOOLS,
	TOOL_SCHEMAS,
	validate
} from '../src/lib/tools/loop.ts';
import {
	isOffer,
	MalformedPlan,
	parseCalls,
	SCRIPTED_SEQUENCE,
	ScriptedDriver,
	type Proposal
} from '../src/lib/model/driver.ts';

/** A step's identity for comparison: the tool it calls, or what it asks for. */
const describeStep = (step: Proposal): string =>
	isOffer(step) ? `ask:${step.widget.kind}` : step.name;

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
	it('never lets standing approval cover a spend step, even when the Consumer said "always" to everything', () => {
		const standing = new Set(TOOLS);
		expect(requiresFreshConsent('place-order', standing)).toBe(true);
		expect(requiresFreshConsent('start-checkout', standing)).toBe(true);
		// cancel-order and request-refund share start-checkout's scope: both
		// change an order's fate, which gets a fresh look every time too.
		expect(requiresFreshConsent('cancel-order', standing)).toBe(true);
		expect(requiresFreshConsent('request-refund', standing)).toBe(true);
		// Reads and basket-building steps can carry standing approval.
		expect(requiresFreshConsent('search', new Set(['search']))).toBe(false);
		expect(requiresFreshConsent('add-line', new Set(['add-line']))).toBe(false);
	});

	it('allows standing approval on everything short of the two scopes that move toward a spend', () => {
		expect([...ALWAYS_ALLOWABLE].sort()).toEqual(
			[
				'add-line',
				'apply-public-code',
				'choose-fulfillment',
				'clear-basket',
				'order-status',
				'read-item',
				'remove-line',
				'search',
				'set-contact',
				'set-destination'
			].sort()
		);
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

	it('READS is a strict subset of ALWAYS_ALLOWABLE, never the other way round', () => {
		// ALWAYS_ALLOWABLE governs standing consent; READS governs which calls
		// may skip the proposal guards and fan out to every shop unattended. A
		// write can be always-allowed by the Consumer without qualifying for
		// either of those — conflating the two once sent an unaddressed
		// add-line to every shop the Consumer had.
		for (const tool of READS) expect(ALWAYS_ALLOWABLE.has(tool)).toBe(true);
		expect(ALWAYS_ALLOWABLE.size).toBeGreaterThan(READS.size);
	});
});

describe('shopsFor — an unaddressed write never reaches a shop it was not meant for', () => {
	const SD = 'spoiledduckie.localhost';
	const DE = 'dogeared.localhost';
	const shops = [
		{ domain: SD, name: 'SpoiledDuckie' },
		{ domain: DE, name: 'Dog-Eared' }
	];
	const seen = catalogueFrom([]);

	it('fans an unaddressed read out to every known shop', () => {
		expect(shopsFor({ name: 'search', args: { query: 'tote' } }, seen, shops).sort()).toEqual(
			[SD, DE].sort()
		);
	});

	it('never fans an unaddressed write out — it has to land somewhere exact', () => {
		expect(() => shopsFor({ name: 'add-line', args: { sku: 'unknown-sku' } }, seen, shops)).toThrow(
			ToolError
		);
	});

	it('continues a checkout already under way rather than re-asking on every step', () => {
		// set-destination carries no sku/group to resolve by — the only reason
		// it can ever resolve unattended is that exactly one shop has a basket.
		const midCheckout = catalogueFrom([
			{ shop: SD, name: 'add-line', args: { sku: 'SD-TOTE-BLK-M' }, result: { lines: [{ sku: 'SD-TOTE-BLK-M', qty: 1 }] } }
		]);
		expect(shopsFor({ name: 'set-destination', args: {} }, midCheckout, shops)).toEqual([SD]);
	});

	it('still asks when two shops both have an open basket', () => {
		const twoBaskets = catalogueFrom([
			{ shop: SD, name: 'add-line', args: { sku: 'SD-TOTE-BLK-M' }, result: { lines: [{ sku: 'SD-TOTE-BLK-M', qty: 1 }] } },
			{ shop: DE, name: 'add-line', args: { sku: 'DE-NOTEBOOK' }, result: { lines: [{ sku: 'DE-NOTEBOOK', qty: 1 }] } }
		]);
		expect(() => shopsFor({ name: 'set-destination', args: {} }, twoBaskets, shops)).toThrow(ToolError);
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
		expect(emitted.map(describeStep)).toEqual(SCRIPTED_SEQUENCE.map(describeStep));
		// The address and the Contact Point are asked for, never pinned: a demo
		// that ships a literal address is where an invented one comes from.
		const asked = SCRIPTED_SEQUENCE.filter(isOffer)
			.map((offer) => offer.widget)
			.filter((widget) => widget.kind === 'form');
		expect(asked.map((widget) => widget.tool)).toEqual(['set-destination', 'set-contact']);
		expect(await driver.plan()).toEqual([]);
	});

	it('is the default, so the demo runs with no model at all', () => {
		expect(new ScriptedDriver().name).toBe('scripted');
	});
});

describe('consent notes', () => {
	it('explains an empty choose-fulfillment as listing, not choosing', () => {
		const note = consentNote({ name: 'choose-fulfillment', args: { id: '' } });
		expect(note).toMatch(/which delivery options/i);
		expect(note).toMatch(/chooses nothing yet/i);
	});

	it('names the option when one is picked', () => {
		const note = consentNote({ name: 'choose-fulfillment', args: { id: 'rest-of-india' } });
		expect(note).toMatch(/rest-of-india/);
		expect(note).toMatch(/approve/i);
	});

	it('says what an add-line puts in the basket', () => {
		const note = consentNote({ name: 'add-line', args: { sku: 'SD-TOTE-BLK-M', qty: 2 } });
		expect(note).toMatch(/SD-TOTE-BLK-M/);
		expect(note).toMatch(/2/);
		expect(note).toMatch(/no money moves/i);
	});

	it('leaves the spend steps to their own copy', () => {
		expect(consentNote({ name: 'start-checkout', args: {} })).toBeNull();
		expect(consentNote({ name: 'place-order', args: {} })).toBeNull();
	});

	it('never quotes, totals, or promises a price', () => {
		const samples: Record<string, Record<string, unknown>> = {
			search: { query: 'tote' },
			'read-item': { group: 'tote' },
			'add-line': { sku: 'SD-TOTE-BLK-M', qty: 1 },
			'remove-line': { sku: 'SD-TOTE-BLK-M' },
			'set-destination': { destination: {} },
			'set-contact': { contact: {} },
			'choose-fulfillment': { id: '' },
			'apply-public-code': { code: 'DIWALI10' },
			'order-status': { order_id: 'o1' },
			'cancel-order': { order_id: 'o1' },
			'request-refund': { order_id: 'o1' }
		};
		for (const tool of TOOLS) {
			const note = consentNote({ name: tool, args: samples[tool] ?? {} });
			expect(note ?? '', tool).not.toMatch(/₹/);
		}
	});

	it('hides args that carry nothing worth reading', () => {
		// The reported case: an empty choose-fulfillment shows the note, not noise.
		expect(hasMeaningfulArgs({ id: '' })).toBe(false);
		expect(hasMeaningfulArgs({})).toBe(false);
		expect(hasMeaningfulArgs({ id: '   ' })).toBe(false);
		expect(hasMeaningfulArgs({ destination: { line1: '', city: null } })).toBe(false);
		expect(hasMeaningfulArgs(null)).toBe(false);
	});

	it('keeps args that carry anything worth reading', () => {
		expect(hasMeaningfulArgs({ id: 'rest-of-india' })).toBe(true);
		expect(hasMeaningfulArgs({ sku: 'SD-TOTE-BLK-M', qty: 1 })).toBe(true);
		// A 0 or false is a value, not an absence — hiding it would mislead.
		expect(hasMeaningfulArgs({ qty: 0 })).toBe(true);
		expect(hasMeaningfulArgs({ destination: { city: 'Chennai' } })).toBe(true);
	});
});

describe('catalogue browsing', () => {
	it('lets search run with no query, for browsing the whole catalogue', () => {
		const parameters = TOOL_SCHEMAS.search.parameters as { required?: string[] };
		expect(parameters.required ?? []).not.toContain('query');
		expect(TOOL_SCHEMAS.search.description).toMatch(/empty/i);
	});

	it('tells the model to browse first for gifts and occasions', () => {
		expect(SYSTEM_PROMPT).toMatch(/birthday gift/i);
		expect(SYSTEM_PROMPT).toMatch(/empty query/i);
	});

	it('forbids recommending products it has not seen', () => {
		expect(SYSTEM_PROMPT).toMatch(/never recommend a\s+product you have not seen/i);
	});

	it('only acts on what the shopper asked for or accepted', () => {
		expect(SYSTEM_PROMPT).toMatch(/only do what the shopper asked for or accepted/i);
		expect(SYSTEM_PROMPT).toMatch(/stop and ask/i);
	});

	it('orders the flow: cart, then destination, then fulfillment, then quote', () => {
		expect(SYSTEM_PROMPT).toMatch(/build the cart completely first/i);
	});

	it('forbids inventing a destination, placeholder or otherwise', () => {
		expect(SYSTEM_PROMPT).toMatch(/not\s+even a placeholder/i);
	});

	it('offers a consented fresh start', () => {
		expect(SCOPES['clear-basket']).toBe('build-basket');
		const parameters = TOOL_SCHEMAS['clear-basket'].parameters as { required?: unknown };
		expect(parameters.required ?? []).toEqual([]);
		expect(consentNote({ name: 'clear-basket', args: {} })).toMatch(/starting over/i);
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


describe('an address the shopper never gave is never sent', () => {
	const SAID = 'I want a tote and a phone charm. Send it to 12 Church Street, Bengaluru 560001.';
	const GIVEN = {
		destination: { line1: '12 Church Street', city: 'Bengaluru', state: 'KA', postal_code: '560001' }
	};

	it('passes an address the shopper typed', () => {
		expect(() => assertDestinationFromConsumer(GIVEN, SAID)).not.toThrow();
	});

	it('ignores punctuation and spacing the shopper used', () => {
		const spaced = { destination: { ...GIVEN.destination, postal_code: '560001' } };
		expect(() => assertDestinationFromConsumer(spaced, 'ship to 12, Church Street — 560 001')).not.toThrow();
	});

	it('refuses a placeholder the model invented', () => {
		const invented = {
			destination: { line1: '123 Your Street', city: 'Bangalore', state: 'KA', postal_code: '560001' }
		};
		expect(() => assertDestinationFromConsumer(invented, SAID)).toThrow(ToolError);
	});

	it('refuses when the shopper has given no address at all', () => {
		expect(() => assertDestinationFromConsumer(GIVEN, 'I want a tote and a phone charm.')).toThrow(
			/address you have given me/
		);
	});
});
