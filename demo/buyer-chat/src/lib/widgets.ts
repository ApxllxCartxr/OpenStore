/**
 * Widgets: the agent proposing an *interface*, not just a sentence.
 *
 * A shop asks for different things — one wants a phone, another an email, a
 * third a full address — so the fields cannot be hard-coded here. The model
 * writes the widget, which is why this file exists: **a widget is a proposal,
 * exactly like a tool call**, and it is validated against a closed grammar
 * before anything renders. A model that emits a field, a tool or a kind that is
 * not in this file changes nothing on the page.
 *
 * Three rules hold, and they are the reason widgets are safe to render:
 *
 * - **No spend tool is ever widget-driven.** `start-checkout` and `place-order`
 *   keep their own consent copy and their own tap; a button that quietly opened
 *   a checkout would be the whole posture undone.
 * - **No amounts.** Nothing the model writes here may carry a price. Costs on a
 *   delivery option are looked up from the shop's own tool result at render
 *   time, so a widget cannot misquote one.
 * - **What the shopper types in a widget is what the shopper said.** A
 *   submission is recorded as their own message, so the transcript shows where
 *   an address came from and the destination guard sees it honestly.
 */
import { ToolError, type Seen, type Shop, type ToolName } from './tools/loop.ts';

/** The tools a widget may drive. The spend path is deliberately absent. */
export const WIDGET_TOOLS = [
	'add-line',
	'remove-line',
	'set-destination',
	'set-contact',
	'choose-fulfillment',
	'apply-public-code'
] as const;

export type WidgetTool = (typeof WIDGET_TOOLS)[number];

/** Input kinds, which are keyboard hints and nothing more — every value
 *  reaches the shop as text and the shop validates it. */
export const FIELD_KINDS = ['text', 'email', 'tel'] as const;

export type WidgetField = {
	name: string;
	label: string;
	kind: (typeof FIELD_KINDS)[number];
	required: boolean;
	placeholder: string;
};

export type WidgetOption = { label: string; value: string };

export type Widget =
	| {
			kind: 'form';
			title: string;
			submit: string;
			tool: WidgetTool;
			/** Nest the fields under one argument — `destination`, `contact` — the
			 *  way the shop's own schema nests them. Empty means top level. */
			group: string;
			fields: WidgetField[];
	  }
	| {
			kind: 'choices';
			title: string;
			tool: WidgetTool;
			/** The argument the chosen value becomes: `sku`, `id`, `code`. */
			arg: string;
			options: WidgetOption[];
			/** Fixed arguments every choice carries, e.g. `{"qty": 1}`. */
			args: Record<string, string | number>;
	  }
	| { kind: 'chips'; options: string[] };

const LIMITS = { fields: 8, options: 8, chips: 6, label: 80, value: 120 };

/** A price the model typed is a price the shop did not sign. Widgets carry
 *  none: costs are rendered from the tool result beside them. */
const LOOKS_LIKE_MONEY = /[₹$€£]|\brs\.?\s*\d|\b\d+\.\d{2}\b/i;

function text(value: unknown, limit: number, what: string): string {
	if (typeof value !== 'string' || !value.trim()) {
		throw new ToolError('widget-invalid', `A widget ${what} is text, and this one is missing.`);
	}
	const trimmed = value.trim();
	if (trimmed.length > limit) {
		throw new ToolError('widget-invalid', `A widget ${what} is at most ${limit} characters.`);
	}
	if (LOOKS_LIKE_MONEY.test(trimmed)) {
		throw new ToolError('widget-invalid', `A widget ${what} may not carry an amount.`);
	}
	return trimmed;
}

function field(raw: unknown): WidgetField {
	const source = (raw ?? {}) as Record<string, unknown>;
	const name = text(source.name, 40, 'field name');
	if (!/^[a-z][a-z0-9_]*$/.test(name)) {
		throw new ToolError('widget-invalid', `${name} is not an argument name this shop could read.`);
	}
	const kind = FIELD_KINDS.includes(source.kind as WidgetField['kind'])
		? (source.kind as WidgetField['kind'])
		: 'text';
	return {
		name,
		label: text(source.label, LIMITS.label, 'field label'),
		kind,
		required: source.required !== false,
		placeholder: typeof source.placeholder === 'string' ? source.placeholder.slice(0, 60) : ''
	};
}

function option(raw: unknown): WidgetOption {
	const source = (raw ?? {}) as Record<string, unknown>;
	return {
		label: text(source.label, LIMITS.label, 'option label'),
		value: text(source.value, LIMITS.value, 'option value')
	};
}

function tool(raw: unknown): WidgetTool {
	if (!WIDGET_TOOLS.includes(raw as WidgetTool)) {
		throw new ToolError(
			'widget-invalid',
			`A widget cannot drive ${String(raw)}. Buttons never open a checkout or place an order.`
		);
	}
	return raw as WidgetTool;
}

function list(raw: unknown, limit: number, what: string): unknown[] {
	if (!Array.isArray(raw) || !raw.length) {
		throw new ToolError('widget-invalid', `A widget needs at least one ${what}.`);
	}
	if (raw.length > limit) {
		throw new ToolError('widget-invalid', `A widget shows at most ${limit} ${what}s.`);
	}
	return raw;
}

/** Fixed arguments a choices widget carries. Primitives only: an object here
 *  would be a second place args could be shaped, out of the model's text. */
function fixedArgs(raw: unknown): Record<string, string | number> {
	if (raw === undefined || raw === null) return {};
	if (typeof raw !== 'object' || Array.isArray(raw)) {
		throw new ToolError('widget-invalid', 'A widget’s fixed arguments are a flat object.');
	}
	const out: Record<string, string | number> = {};
	for (const [key, value] of Object.entries(raw as Record<string, unknown>)) {
		if (typeof value === 'string') out[key] = text(value, LIMITS.value, 'argument');
		else if (typeof value === 'number' && Number.isFinite(value)) out[key] = value;
		else throw new ToolError('widget-invalid', `${key} is not a value a widget can carry.`);
	}
	return out;
}

/** Validate one widget proposal. Throws `ToolError` rather than returning a
 *  half-built widget: a malformed proposal renders nothing at all. */
export function parseWidget(raw: unknown): Widget {
	const source = (raw ?? {}) as Record<string, unknown>;
	if (source.kind === 'form') {
		return {
			kind: 'form',
			title: text(source.title, LIMITS.label, 'title'),
			submit: typeof source.submit === 'string' ? text(source.submit, 30, 'button') : 'Send',
			tool: tool(source.tool),
			group: typeof source.group === 'string' ? source.group.trim() : '',
			fields: list(source.fields, LIMITS.fields, 'field').map(field)
		};
	}
	if (source.kind === 'choices') {
		return {
			kind: 'choices',
			title: typeof source.title === 'string' ? text(source.title, LIMITS.label, 'title') : '',
			tool: tool(source.tool),
			arg: text(source.arg, 40, 'argument name'),
			options: list(source.options, LIMITS.options, 'option').map(option),
			args: fixedArgs(source.args)
		};
	}
	if (source.kind === 'chips') {
		return {
			kind: 'chips',
			options: list(source.options, LIMITS.chips, 'chip').map((c) => text(c, LIMITS.label, 'chip'))
		};
	}
	throw new ToolError('widget-invalid', `${String(source.kind)} is not a widget this chat renders.`);
}

/**
 * The interface a refusal implies, built here rather than asked of the model.
 *
 * A refused proposal used to go back as text and the model tried again: an
 * invented address, refused, then the same call with empty strings, refused
 * again, and only then the question — two model turns and two junk tool cards
 * to arrive at a form this file can write deterministically. What the
 * Consumer has to supply is known from the refusal itself, so it is offered
 * directly.
 *
 * Returns null when a refusal implies no interface; the sentence then stands
 * on its own, which is the fallback every widget is a shortcut for.
 */
export function widgetForRefusal(
	code: string,
	call: { name: string; args: Record<string, unknown> },
	seen: Seen,
	fields: Record<string, unknown> = {}
): Widget | null {
	if (code === 'shop-required') {
		const candidates = Array.isArray(fields.candidates) ? (fields.candidates as Shop[]) : [];
		// shop-required only ever comes from a write, and only the tools a
		// widget can drive in the first place are ones this can offer to
		// retry — a refused cancel-order or request-refund still says which
		// shops could answer, just as a sentence rather than a tap.
		if (candidates.length < 2 || !WIDGET_TOOLS.includes(call.name as WidgetTool)) return null;
		const fixedArgs: Record<string, string | number> = {};
		for (const [key, value] of Object.entries(call.args)) {
			if (typeof value === 'string' || typeof value === 'number') fixedArgs[key] = value;
		}
		return {
			kind: 'choices',
			title: 'Which shop?',
			tool: call.name as WidgetTool,
			arg: 'shop',
			options: candidates.map((shop) => ({ label: shop.name, value: shop.domain })),
			args: fixedArgs
		};
	}
	if (code === 'destination-not-given') {
		return {
			kind: 'form',
			title: 'Delivery address',
			submit: 'Use this address',
			tool: 'set-destination',
			group: 'destination',
			fields: [
				{ name: 'line1', label: 'Street address', kind: 'text', required: true, placeholder: '' },
				{ name: 'city', label: 'City', kind: 'text', required: true, placeholder: '' },
				{ name: 'state', label: 'State code', kind: 'text', required: true, placeholder: 'KA' },
				{ name: 'postal_code', label: 'PIN code', kind: 'text', required: true, placeholder: '' }
			]
		};
	}
	if (code !== 'variant-required' || call.name !== 'add-line') return null;
	// The group behind the refused SKU — or the refused id itself, when what
	// was proposed was a group rather than something that can be bought.
	const sku = String(call.args.sku ?? '');
	const group = seen.variants.get(sku)?.group ?? (seen.groups.has(sku) ? sku : null);
	if (!group) return null;
	const options: WidgetOption[] = [];
	for (const [variantSku, variant] of seen.variants) {
		if (variant.group !== group) continue;
		options.push({ label: variant.name, value: variantSku });
	}
	if (options.length < 2 || options.length > LIMITS.options) return null;
	const qty = typeof call.args.qty === 'number' ? call.args.qty : 1;
	return {
		kind: 'choices',
		title: 'Which one?',
		tool: 'add-line',
		arg: 'sku',
		options,
		args: { qty }
	};
}

/** ```widget … ``` — the one place a model may put an interface. Fenced so it
 *  can never be mistaken for something to say to the shopper. */
const BLOCK = /```widget\s*([\s\S]*?)```/;

export type Composed = { text: string; widget: Widget | null; refusal: string | null };

/**
 * Split a model's message into what it says and what it offers.
 *
 * A widget that does not parse is dropped and the sentence still renders: the
 * shopper reads the question and types the answer, which is the fallback every
 * widget is only ever a shortcut for.
 */
export function composeMessage(raw: string): Composed {
	const found = BLOCK.exec(raw);
	if (!found) return { text: raw.trim(), widget: null, refusal: null };
	const said = raw.replace(BLOCK, '').trim();
	try {
		return { text: said, widget: parseWidget(JSON.parse(found[1] ?? '')), refusal: null };
	} catch (error) {
		return { text: said, widget: null, refusal: (error as Error).message };
	}
}

/** The args a submission becomes, built from the *stored* widget definition and
 *  nothing the browser sent: a form posts values, never field names or a tool.
 */
export function argsFor(
	widget: Widget,
	values: Record<string, string>
): { tool: ToolName; args: Record<string, unknown> } {
	if (widget.kind === 'chips') {
		throw new ToolError('widget-invalid', 'Chips are things to say, not calls to make.');
	}
	if (widget.kind === 'form') {
		const filled: Record<string, string> = {};
		for (const one of widget.fields) {
			const value = (values[one.name] ?? '').trim();
			if (!value) {
				if (one.required) {
					throw new ToolError('widget-incomplete', `${one.label} is still empty.`);
				}
				continue;
			}
			filled[one.name] = value;
		}
		return {
			tool: widget.tool as ToolName,
			args: widget.group ? { [widget.group]: filled } : filled
		};
	}
	const chosen = widget.options.find((o) => o.value === (values[widget.arg] ?? ''));
	if (!chosen) {
		throw new ToolError('widget-invalid', 'That is not one of the options offered.');
	}
	return { tool: widget.tool as ToolName, args: { ...widget.args, [widget.arg]: chosen.value } };
}

/**
 * What the shopper just said, in their own submission's words.
 *
 * Recorded as a Consumer message before the call runs. It is how an address
 * typed into a form counts as given — and how anyone reading the transcript can
 * see that it was.
 */
export function saidBySubmitting(widget: Widget, values: Record<string, string>): string {
	if (widget.kind === 'form') {
		const parts = widget.fields
			.map((one) => (values[one.name] ?? '').trim())
			.filter((value) => value.length > 0);
		return `${widget.title}: ${parts.join(', ')}`;
	}
	if (widget.kind === 'choices') {
		const chosen = widget.options.find((o) => o.value === (values[widget.arg] ?? ''));
		return chosen ? chosen.label : '';
	}
	return '';
}
