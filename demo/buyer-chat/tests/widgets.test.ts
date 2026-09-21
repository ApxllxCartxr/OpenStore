/**
 * Widgets are model output, so they are tested the way tool calls are: what a
 * malformed or overreaching proposal does (nothing), and what a submission
 * turns into (a call the shop would recognise, built from the stored
 * definition rather than from the post).
 */
import { describe, expect, it } from 'vitest';
import {
	argsFor,
	composeMessage,
	parseWidget,
	saidBySubmitting,
	WIDGET_TOOLS,
	type Widget
} from '../src/lib/widgets.ts';
import { assertDestinationFromConsumer, ToolError } from '../src/lib/tools/loop.ts';

const ADDRESS: Widget = parseWidget({
	kind: 'form',
	title: 'Delivery address',
	submit: 'Use this address',
	tool: 'set-destination',
	group: 'destination',
	fields: [
		{ name: 'line1', label: 'Street address', kind: 'text' },
		{ name: 'city', label: 'City', kind: 'text' },
		{ name: 'state', label: 'State code', kind: 'text' },
		{ name: 'postal_code', label: 'PIN code', kind: 'text' }
	]
});

const TOTES: Widget = parseWidget({
	kind: 'choices',
	title: 'Which tote?',
	tool: 'add-line',
	arg: 'sku',
	args: { qty: 1 },
	options: [
		{ label: 'Red / L', value: 'SD-TOTE-RED-L' },
		{ label: 'Red / M', value: 'SD-TOTE-RED-M' }
	]
});

describe('a widget is a proposal, validated like any other', () => {
	it('refuses a kind this chat does not render', () => {
		expect(() => parseWidget({ kind: 'iframe', src: 'https://elsewhere.test' })).toThrow(ToolError);
	});

	it('refuses to drive a spend tool, whatever the model asks for', () => {
		expect(WIDGET_TOOLS).not.toContain('start-checkout');
		expect(WIDGET_TOOLS).not.toContain('place-order');
		expect(() => parseWidget({ kind: 'choices', tool: 'place-order', arg: 'id', options: [{ label: 'Buy', value: 'x' }] })).toThrow(
			/never open a checkout/
		);
	});

	it('refuses an amount anywhere in its text', () => {
		expect(() =>
			parseWidget({
				kind: 'choices',
				tool: 'choose-fulfillment',
				arg: 'id',
				options: [{ label: 'Karnataka — ₹49', value: 'karnataka' }]
			})
		).toThrow(/may not carry an amount/);
	});

	it('refuses a field name the shop could not read', () => {
		expect(() =>
			parseWidget({
				kind: 'form',
				title: 'Contact',
				tool: 'set-contact',
				fields: [{ name: 'e-mail!', label: 'Email', kind: 'email' }]
			})
		).toThrow(ToolError);
	});

	it('takes the fields the shop at hand actually asks for', () => {
		// The point of letting the model write these: one shop wants a phone,
		// another an email, and neither is hard-coded anywhere.
		const phone = parseWidget({
			kind: 'form',
			title: 'Where should the shop send updates?',
			tool: 'set-contact',
			group: 'contact',
			fields: [{ name: 'phone', label: 'Phone', kind: 'tel' }]
		});
		expect(argsFor(phone, { phone: '+919000000001' })).toEqual({
			tool: 'set-contact',
			args: { contact: { phone: '+919000000001' } }
		});
	});
});

describe('a message carries at most one widget, fenced', () => {
	it('splits what was said from what was offered', () => {
		const said = composeMessage(
			'Which one would you like?\n\n```widget\n{"kind":"chips","options":["Red / L","Red / M"]}\n```'
		);
		expect(said.text).toBe('Which one would you like?');
		expect(said.widget?.kind).toBe('chips');
	});

	it('keeps the sentence when the widget does not parse', () => {
		const said = composeMessage('Which size?\n\n```widget\n{"kind":"form"}\n```');
		expect(said.text).toBe('Which size?');
		expect(said.widget).toBeNull();
		expect(said.refusal).toMatch(/widget/i);
	});

	it('leaves an ordinary message alone', () => {
		expect(composeMessage('The shop has two totes in red.')).toEqual({
			text: 'The shop has two totes in red.',
			widget: null,
			refusal: null
		});
	});
});

describe('a submission becomes a call the shop would recognise', () => {
	it('nests a form the way the shop nests it, and keeps the fixed args', () => {
		expect(
			argsFor(ADDRESS, {
				line1: '12 Church Street',
				city: 'Bengaluru',
				state: 'KA',
				postal_code: '560001'
			})
		).toEqual({
			tool: 'set-destination',
			args: {
				destination: {
					line1: '12 Church Street',
					city: 'Bengaluru',
					state: 'KA',
					postal_code: '560001'
				}
			}
		});
		expect(argsFor(TOTES, { sku: 'SD-TOTE-RED-L' })).toEqual({
			tool: 'add-line',
			args: { qty: 1, sku: 'SD-TOTE-RED-L' }
		});
	});

	it('refuses a value that was never one of the options', () => {
		// The definition is read back from the database for exactly this: a post
		// naming a SKU nobody was offered buys nothing.
		expect(() => argsFor(TOTES, { sku: 'SD-CHARMBAR-SEAT' })).toThrow(/not one of the options/);
	});

	it('refuses a half-filled form rather than sending a partial address', () => {
		expect(() => argsFor(ADDRESS, { line1: '12 Church Street', city: '', state: '', postal_code: '' })).toThrow(
			/still empty/
		);
	});

	it('is recorded as the shopper’s own words, so a typed address counts as given', () => {
		const values = {
			line1: '12 Church Street',
			city: 'Bengaluru',
			state: 'KA',
			postal_code: '560001'
		};
		const said = saidBySubmitting(ADDRESS, values);
		expect(said).toContain('12 Church Street');
		// The guard that refuses an invented address must pass on this one, since
		// the shopper typed every character of it.
		expect(() => assertDestinationFromConsumer(argsFor(ADDRESS, values).args, said)).not.toThrow();
	});
});
