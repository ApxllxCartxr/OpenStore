/**
 * The two refusals a shop cannot make for us, and the interfaces they imply.
 *
 * A SKU is a SKU to the shop, so "cap in M" and "a cap" reach it identically —
 * which is how a size the shopper never picked got into a basket. The address
 * guard already existed; what is new is that neither refusal goes back to the
 * model to recover from.
 */
import { describe, expect, it } from 'vitest';
import {
	assertVariantChosenByConsumer,
	catalogueFrom,
	ToolError,
	type SeenCall
} from '../src/lib/tools/loop.ts';
import { widgetForRefusal } from '../src/lib/widgets.ts';

const SD = 'spoiledduckie.localhost';
const DE = 'dogeared.localhost';

const CAP_READ: SeenCall = {
	shop: SD,
	name: 'read-item',
	args: { group: 'cap' },
	result: {
		group: 'cap',
		option_axes: { size: ['S', 'M'] },
		variants: [
			{ sku: 'SD-CAP-S', name: 'Cap — S', options: { size: 'S' }, availability: 'in-stock', tags: [] },
			{ sku: 'SD-CAP-M', name: 'Cap — M', options: { size: 'M' }, availability: 'low-stock', tags: [] }
		]
	}
};
const SEAT_READ: SeenCall = {
	shop: SD,
	name: 'read-item',
	args: { group: 'charmbar' },
	result: {
		group: 'charmbar',
		option_axes: {},
		variants: [
			{ sku: 'SD-CHARMBAR-SEAT', name: 'Charm-bar seat', options: {}, availability: 'in-stock', tags: ['service'] }
		]
	}
};
const WRAP_READ: SeenCall = {
	shop: SD,
	name: 'read-item',
	args: { group: 'giftwrap' },
	result: {
		group: 'giftwrap',
		option_axes: {},
		variants: [{ sku: 'SD-GIFTWRAP', name: 'Gift-wrap', options: {}, tags: ['addon'] }]
	}
};

const seen = catalogueFrom([CAP_READ, SEAT_READ, WRAP_READ]);

describe('a variant the shopper never chose', () => {
	it('refuses a size picked for them', () => {
		expect(() => assertVariantChosenByConsumer('SD-CAP-M', seen, 'I want a cap and a Charm Bar Seat.')).toThrow(
			ToolError
		);
	});

	it('does not read the M out of an unrelated word', () => {
		// "I'm", "Mumbai", "medium" — a substring match would let any of them
		// stand in for the size, which is why matching is token-wise.
		expect(() => assertVariantChosenByConsumer('SD-CAP-M', seen, 'I am in Mumbai')).toThrow(ToolError);
	});

	it('allows a size the shopper named', () => {
		expect(() => assertVariantChosenByConsumer('SD-CAP-M', seen, 'a cap in M please')).not.toThrow();
	});

	it('allows the SKU itself, and a tapped option label', () => {
		expect(() => assertVariantChosenByConsumer('SD-CAP-M', seen, 'add SD-CAP-M')).not.toThrow();
		// What `saidBySubmitting` records when the choices widget is tapped.
		expect(() => assertVariantChosenByConsumer('SD-CAP-M', seen, 'Cap — M')).not.toThrow();
	});

	it('never stands in the way of a group with one variant', () => {
		expect(() =>
			assertVariantChosenByConsumer('SD-CHARMBAR-SEAT', seen, 'I want a Charm Bar Seat.')
		).not.toThrow();
	});

	it('says nothing about a SKU this conversation has not read', () => {
		expect(() => assertVariantChosenByConsumer('SD-UNSEEN', seen, 'whatever')).not.toThrow();
	});
});

describe('what the transcript knows about the catalogue', () => {
	it('learns groups, add-ons and the live basket from recorded results', () => {
		const withBasket = catalogueFrom([
			CAP_READ,
			WRAP_READ,
			{ shop: SD, name: 'add-line', args: { sku: 'SD-CAP-S' }, result: { lines: [{ sku: 'SD-CAP-S', qty: 1 }] } }
		]);
		expect(withBasket.groups.has('cap')).toBe(true);
		expect(withBasket.addons.has('SD-GIFTWRAP')).toBe(true);
		expect(withBasket.addons.has('SD-CAP-S')).toBe(false);
		expect([...(withBasket.baskets.get(SD) ?? [])]).toEqual(['SD-CAP-S']);
	});

	it('learns that a group has axes from search alone', () => {
		const fromSearch = catalogueFrom([
			{
				shop: SD,
				name: 'search',
				args: { query: 'cap' },
				result: { results: [{ group: 'cap', option_axes: { size: ['S', 'M'] } }] }
			}
		]);
		expect(fromSearch.axes.get('cap')).toEqual(['size']);
	});
});

describe('the interface a refusal implies', () => {
	it('offers the address form instead of asking the model again', () => {
		const widget = widgetForRefusal('destination-not-given', { name: 'set-destination', args: {} }, seen);
		expect(widget?.kind).toBe('form');
		if (widget?.kind !== 'form') throw new Error('unreachable');
		expect(widget.tool).toBe('set-destination');
		expect(widget.group).toBe('destination');
		expect(widget.fields.map((f) => f.name)).toEqual(['line1', 'city', 'state', 'postal_code']);
	});

	it('offers the sizes when the refusal was an unchosen variant', () => {
		const widget = widgetForRefusal('variant-required', { name: 'add-line', args: { sku: 'SD-CAP-M', qty: 2 } }, seen);
		expect(widget?.kind).toBe('choices');
		if (widget?.kind !== 'choices') throw new Error('unreachable');
		expect(widget.tool).toBe('add-line');
		expect(widget.options.map((o) => o.value).sort()).toEqual(['SD-CAP-M', 'SD-CAP-S']);
		// The quantity the model asked for survives the correction.
		expect(widget.args).toEqual({ qty: 2 });
	});

	it('offers the variants when a group id was proposed as a SKU', () => {
		const widget = widgetForRefusal('variant-required', { name: 'add-line', args: { sku: 'cap' } }, seen);
		expect(widget?.kind).toBe('choices');
	});

	it('offers nothing for a refusal with no interface behind it', () => {
		expect(widgetForRefusal('sold-out', { name: 'add-line', args: { sku: 'SD-CAP-M' } }, seen)).toBeNull();
		// One variant is not a choice.
		expect(
			widgetForRefusal('variant-required', { name: 'add-line', args: { sku: 'SD-CHARMBAR-SEAT' } }, seen)
		).toBeNull();
	});
});
