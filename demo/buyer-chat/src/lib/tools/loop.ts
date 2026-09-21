/**
 * The tool loop: bounded steps, closed action set, deterministic validation.
 *
 * **The agent never sums, estimates, or re-labels a line.** Agent-side totals do
 * not exist — every number the Consumer sees comes from a Merchant-signed
 * Quote, rendered verbatim. A chat that adds its own subtotal has invented a
 * second money path with a friendly tone.
 *
 * Model output is a *proposal*. Every call is validated against the closed set
 * and against real Merchant data before anything renders or is sent, and a
 * malformed plan changes nothing: never a partial application of the calls that
 * happened to parse.
 */
import type { ToolCall } from '../model/driver.ts';

/** The closed action set. Mirrors the sidecar's `ToolName`; a name here and not
 *  there is refused at the door. */
export const TOOLS = [
	'search',
	'read-item',
	'add-line',
	'remove-line',
	'set-destination',
	'set-contact',
	'choose-fulfillment',
	'apply-public-code',
	'start-checkout',
	'place-order',
	'order-status',
	'cancel-order',
	'request-refund'
] as const;

export type ToolName = (typeof TOOLS)[number];

export const SCOPES: Record<ToolName, string> = {
	search: 'search',
	'read-item': 'search',
	'add-line': 'build-basket',
	'remove-line': 'build-basket',
	'set-destination': 'build-basket',
	'set-contact': 'build-basket',
	'choose-fulfillment': 'build-basket',
	'apply-public-code': 'build-basket',
	'start-checkout': 'start-checkout',
	'place-order': 'confirm',
	'order-status': 'search',
	'cancel-order': 'start-checkout',
	'request-refund': 'start-checkout'
};

/** Reads and drafts only. **Standing approval never covers a spend step** — any
 *  cart-with-spend, checkout, or place-order needs a fresh Allow-once modal
 *  plus a fresh tap (ADR-0008). */
export const ALWAYS_ALLOWABLE: ReadonlySet<string> = new Set([
	'search',
	'read-item',
	'order-status'
]);

export const MAX_STEPS = 24;

export class ToolError extends Error {
	constructor(
		readonly code: string,
		message: string
	) {
		super(message);
	}
}

export type Permission = 'allow-once' | 'always' | 'declined';

export type PermissionRequest = {
	tool: ToolName;
	scope: string;
	/** The **exact request JSON**, shown to the Consumer before they decide. A
	 *  modal that summarises is a modal that can be wrong. */
	request: Record<string, unknown>;
	spend: boolean;
};

export function requiresFreshConsent(tool: ToolName, standing: ReadonlySet<string>): boolean {
	if (!ALWAYS_ALLOWABLE.has(tool)) return true;
	return !standing.has(tool);
}

export function permissionRequest(call: ToolCall): PermissionRequest {
	const tool = call.name as ToolName;
	return {
		tool,
		scope: SCOPES[tool],
		request: call.args,
		// `start-checkout` and `place-order` are the spend path. The modal copy
		// for these grants a **scope and never an amount**: no total exists yet,
		// and a modal that reads like an amount approval teaches the Consumer to
		// click through the tap that is one.
		spend: tool === 'start-checkout' || tool === 'place-order'
	};
}

export function validate(call: ToolCall): ToolCall {
	if (!TOOLS.includes(call.name as ToolName)) {
		throw new ToolError('unknown-tool', `${call.name} is not a tool this agent has.`);
	}
	const tool = call.name as ToolName;
	const args = call.args;

	if (tool === 'add-line') {
		const sku = args.sku;
		if (typeof sku !== 'string' || !sku) {
			throw new ToolError('variant-required', 'Pick the exact option before adding it.');
		}
		const qty = args.qty ?? 1;
		if (!Number.isInteger(qty) || (qty as number) < 1) {
			throw new ToolError('invalid-qty', 'Quantity is a whole number, at least one.');
		}
	}

	if (tool === 'apply-public-code' && typeof args.code !== 'string') {
		throw new ToolError('code-invalid', 'That code did not apply.');
	}

	return { name: tool, args };
}

/**
 * A group id where a SKU belongs refuses. **The chat never picks a size for the
 * Consumer** — a guess wearing a helpful expression is still a guess, and it is
 * the Consumer's money.
 */
export function assertResolvedVariant(sku: string, knownGroups: ReadonlySet<string>): void {
	if (knownGroups.has(sku)) {
		throw new ToolError(
			'variant-required',
			'That is a product, not a specific one. Choose the options first.'
		);
	}
}

/** An Add-on attaches to a **named parent line at the moment it is added**. */
export function assertAddonHasParent(
	sku: string,
	parent: unknown,
	addonSkus: ReadonlySet<string>,
	basketSkus: ReadonlySet<string>
): void {
	if (!addonSkus.has(sku)) return;
	if (typeof parent !== 'string' || !basketSkus.has(parent)) {
		throw new ToolError(
			'addon-without-parent',
			'That attaches to something in the basket. Add the item it goes with first.'
		);
	}
}

export function boundedSteps(steps: number): void {
	if (steps > MAX_STEPS) {
		throw new ToolError('step-limit', `This is taking too many steps (${MAX_STEPS} is the limit).`);
	}
}

/**
 * JSON Schemas for the tool set, for a model that calls tools natively.
 *
 * The sidecar's `_SCHEMAS` are a compact human listing (`"qty": "integer"`);
 * these are what an OpenAI-style `tools` array needs. They are deliberately
 * strict — a model that cannot express a call is better than one that invents
 * a plausible-looking argument the shop will refuse.
 */
export const TOOL_SCHEMAS: Record<ToolName, { description: string; parameters: object }> = {
	search: {
		description: "Search this shop's catalogue by words the shopper used.",
		parameters: {
			type: 'object',
			properties: { query: { type: 'string' } },
			required: ['query']
		}
	},
	'read-item': {
		description: 'Read one product group: its options, variants, prices and availability.',
		parameters: {
			type: 'object',
			properties: { group: { type: 'string', description: 'The group id from search.' } },
			required: ['group']
		}
	},
	'add-line': {
		description:
			'Add one resolved variant (a SKU, never a group) to the basket. Add-ons take the ' +
			'SKU they hang off as `parent`.',
		parameters: {
			type: 'object',
			properties: {
				sku: { type: 'string' },
				qty: { type: 'integer', minimum: 1 },
				parent: { type: 'string' }
			},
			required: ['sku', 'qty']
		}
	},
	'remove-line': {
		description: 'Remove a line from the basket.',
		parameters: {
			type: 'object',
			properties: { sku: { type: 'string' } },
			required: ['sku']
		}
	},
	'set-destination': {
		description: 'Set where the order is delivered. Ask the shopper; never invent an address.',
		parameters: {
			type: 'object',
			properties: {
				destination: {
					type: 'object',
					properties: {
						line1: { type: 'string' },
						city: { type: 'string' },
						state: { type: 'string', description: 'Two-letter Indian state code, e.g. KA.' },
						postal_code: { type: 'string' }
					},
					required: ['line1', 'city', 'state', 'postal_code']
				}
			},
			required: ['destination']
		}
	},
	'set-contact': {
		description: 'Set the email or phone the shop notifies. Ask the shopper; never invent one.',
		parameters: {
			type: 'object',
			properties: {
				contact: {
					type: 'object',
					properties: { email: { type: 'string' }, phone: { type: 'string' } }
				}
			},
			required: ['contact']
		}
	},
	'choose-fulfillment': {
		description: 'Choose a delivery option. Call with no id first to see what is offered.',
		parameters: { type: 'object', properties: { id: { type: 'string' } } }
	},
	'apply-public-code': {
		description: 'Apply an advertised discount code. Private codes are entered by the shopper on the approve page, never here.',
		parameters: {
			type: 'object',
			properties: { code: { type: 'string' } },
			required: ['code']
		}
	},
	'start-checkout': {
		description:
			'Ask the shop to quote and open a checkout. Needs lines, a destination, a contact and ' +
			'a chosen delivery option.',
		parameters: {
			type: 'object',
			properties: {
				method: { type: 'string', enum: ['upi', 'cash-on-delivery'] }
			}
		}
	},
	'place-order': {
		description:
			'Get the approval link for the started checkout. This does NOT place an order — the ' +
			'shopper approves the exact amount on the shop’s own page.',
		parameters: { type: 'object', properties: {} }
	},
	'order-status': {
		description: 'Check the status of an order.',
		parameters: {
			type: 'object',
			properties: { order_id: { type: 'string' } },
			required: ['order_id']
		}
	},
	'cancel-order': {
		description: 'Cancel an order before any money moves.',
		parameters: {
			type: 'object',
			properties: { order_id: { type: 'string' }, reason: { type: 'string' } },
			required: ['order_id']
		}
	},
	'request-refund': {
		description: 'Ask the shop to refund an order.',
		parameters: {
			type: 'object',
			properties: { order_id: { type: 'string' }, reason: { type: 'string' } },
			required: ['order_id']
		}
	}
};

/** What the agent may and may not do, stated to the model in its own terms. */
export const SYSTEM_PROMPT = `You are a shopping agent talking to one shop through its tools.

Hard rules, and they are not style preferences:
- You NEVER state a price, total, tax, discount or stock count that did not come back from a tool. If you do not have it, call a tool or say you do not know.
- You NEVER compute or estimate a total. The shop's own quote is the only total.
- You CANNOT pay. \`place-order\` returns a link the shopper approves on the shop's own page; you hold no payment credential. Say so plainly rather than implying you can buy.
- You NEVER invent a delivery address, an email or a phone number. Ask for them.
- A SKU is what can be bought; a group cannot. Resolve options first with read-item.
- When a tool refuses, tell the shopper the shop's reason in plain words. Do not retry blindly.

Be brief. One or two sentences per turn.`;
