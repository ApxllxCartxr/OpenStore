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
	'clear-basket',
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
	'clear-basket': 'build-basket',
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

/** Every tool outside the two scopes that ever move a basket toward a spend
 *  (`start-checkout`, `confirm`) — reads, and every basket-building step
 *  short of pricing it. **Standing approval never covers a spend step**, and
 *  neither does it cover `cancel-order`/`request-refund`: both share
 *  `start-checkout`'s scope because they change an order's fate, which
 *  deserves a fresh look each time rather than a rubber stamp. Mirrors the
 *  sidecar's own `moneyPathHint` (ADR-0026) — the boundary is the scope
 *  ladder, not a hand-picked list, so a new tool lands on the right side of
 *  it without this set being edited. */
export const ALWAYS_ALLOWABLE: ReadonlySet<string> = new Set(
	(Object.keys(SCOPES) as ToolName[]).filter(
		(tool) => SCOPES[tool] !== 'start-checkout' && SCOPES[tool] !== 'confirm'
	)
);

/** Reads only — `search`, `read-item`, `order-status`. Not the same set as
 *  `ALWAYS_ALLOWABLE` above: a write can be always-allowed by the Consumer
 *  without being safe to run with no proposal guards or fan out to every
 *  shop the Consumer has. Change nothing, so batching them concurrently, or
 *  sending one with no shop named to every shop at once, costs only the
 *  round trips. */
export const READS: ReadonlySet<string> = new Set(['search', 'read-item', 'order-status']);

export const MAX_STEPS = 24;

export class ToolError extends Error {
	constructor(
		readonly code: string,
		message: string,
		/** Structured data a caller needs beyond the message text — e.g.
		 *  `shop-required`'s candidate shops, so the UI can offer them as
		 *  taps rather than asking the shopper to retype a domain. */
		readonly fields: Record<string, unknown> = {}
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

export function requiresFreshConsent(
	tool: string,
	standing: ReadonlySet<string>,
	shops: readonly Shop[] = []
): boolean {
	// The closed set keeps its own hand-reasoned boundary (ALWAYS_ALLOWABLE,
	// above) rather than being folded into isMoneyPath's generic one — they
	// agree today (both are exactly "outside start-checkout/confirm"), but
	// the closed set's boundary is allowed to diverge from the generic
	// default without touching this function.
	const allowable = (TOOLS as readonly string[]).includes(tool) ? ALWAYS_ALLOWABLE.has(tool) : !isMoneyPath(tool, shops);
	if (!allowable) return true;
	return !standing.has(tool);
}

/** True when the args carry anything worth showing. Empty strings,
 *  whitespace-only strings, nulls, and objects/arrays holding only those are
 *  noise — `{"id": ""}` tells the Consumer nothing the note beside it does
 *  not already say. Numbers and booleans always count: a `0` or `false` is a
 *  value, not an absence. */
function isMeaningfulValue(value: unknown): boolean {
	if (value === null || value === undefined) return false;
	if (typeof value === 'string') return value.trim().length > 0;
	if (typeof value === 'number' || typeof value === 'boolean') return true;
	if (Array.isArray(value)) return value.some(isMeaningfulValue);
	if (typeof value === 'object') {
		return Object.values(value as Record<string, unknown>).some(isMeaningfulValue);
	}
	return false;
}

export function hasMeaningfulArgs(args: Record<string, unknown> | null | undefined): boolean {
	if (!args || typeof args !== 'object') return false;
	return Object.values(args).some(isMeaningfulValue);
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

/**
 * One plain-language line for the consent prompt, per tool and arguments.
 *
 * The prompt still shows the exact request JSON beside this note — a note that
 * *replaced* the JSON could be wrong with no way to tell. This says what the
 * call does in consumer words; the JSON beside it is what is actually sent.
 * No amounts: nothing here quotes, totals, or promises a price.
 *
 * `start-checkout` and `place-order` return null: the prompt carries its own
 * spend-path copy for those, and sharing it would drift into two versions.
 */
export function consentNote(call: ToolCall): string | null {
	const tool = call.name as ToolName;
	const args = call.args ?? {};
	const text = (value: unknown): string => (typeof value === 'string' ? value : '');
	switch (tool) {
		case 'search':
			return 'This searches the shop catalogue. Reads only — nothing in your basket changes.';
		case 'read-item':
			return 'This reads one product’s options, variants and prices. Reads only.';
		case 'order-status':
			return 'This checks the status of an order. Reads only.';
		case 'add-line': {
			const qty = typeof args.qty === 'number' && Number.isInteger(args.qty) ? args.qty : 1;
			const sku = text(args.sku) || 'that item';
			const parent = text(args.parent);
			return (
				`This adds ${qty} × ${sku} to the basket` +
				(parent ? `, attaching it to ${parent}` : '') +
				'. No money moves — the exact total still needs your approval on the shop’s own page.'
			);
		}
		case 'remove-line':
			return `This removes ${text(args.sku) || 'that line'} from the basket. Everything else stays as it is.`;
		case 'clear-basket':
			return 'This empties the whole basket — lines, address, contact, delivery choice and code all go. Only for starting over.';
		case 'set-destination':
			return 'This sets the delivery address the shop quotes delivery against. No order is placed and nothing is charged.';
		case 'set-contact':
			return 'This sets the email or phone the shop notifies about your order. Nothing is charged.';
		case 'choose-fulfillment': {
			const id = text(args.id);
			if (!id) {
				return 'This asks the shop which delivery options serve your address and basket. It chooses nothing yet — you will pick one next.';
			}
			return `This picks the ‘${id}’ delivery option. Its cost goes into your total, which you still approve on the shop’s own page.`;
		}
		case 'apply-public-code': {
			const code = text(args.code);
			return (
				(code ? `This applies the public discount code ‘${code}’ to the basket. ` : 'This applies a public discount code to the basket. ') +
				'Private codes are typed on the shop’s approve page, never here.'
			);
		}
		case 'cancel-order': {
			const id = text(args.order_id);
			return `This asks the shop to cancel ${id ? `order ‘${id}’` : 'the order'} before any money moves.`;
		}
		case 'request-refund': {
			const id = text(args.order_id);
			return `This asks the shop to refund ${id ? `order ‘${id}’` : 'the order'}. The merchant decides — Miro cannot move money itself.`;
		}
		default:
			return null;
	}
}

/** Letters and digits only, separators dropped: "560 001" and "560001" are the
 *  same postal code, and "12, Church Street —" is the same line as "12 Church
 *  Street". Comparing with the separators in would refuse an address the
 *  shopper did give, punctuated their own way. */
function normalise(text: string): string {
	return text.toLowerCase().replace(/[^a-z0-9]+/g, '');
}

/**
 * A Destination the shopper never gave is refused **before it is proposed**.
 *
 * A model with an address-shaped hole in its plan fills it: a placeholder
 * street and a plausible pin code reached a real basket this way, and the
 * consent prompt then showed that address as though the shopper had given it.
 * The shopper's own words are the only source of an address this agent has, so
 * the street line and the postal code both have to appear in them. An address
 * the shopper picks from somewhere else — a saved one, a form — arrives as
 * their words too, because they chose it.
 */
export function assertDestinationFromConsumer(
	args: Record<string, unknown>,
	consumerSaid: string
): void {
	const destination = (args.destination ?? args) as Record<string, unknown>;
	const said = normalise(consumerSaid);
	const given = (value: unknown): boolean => {
		const text = typeof value === 'string' ? normalise(value) : '';
		return text.length > 0 && said.includes(text);
	};
	if (given(destination.line1) && given(destination.postal_code)) return;
	throw new ToolError(
		'destination-not-given',
		'I can only send an address you have given me. What address should this go to?'
	);
}

/**
 * What this conversation has actually seen of the shop's catalogue.
 *
 * Built from the recorded tool results rather than from a fetch of its own:
 * the only catalogue facts this agent may act on are the ones it can point at
 * in the transcript. A SKU nobody read about is not refused here — the shop
 * is the authority on what it sells — but a group, an add-on or an unchosen
 * variant that *is* visible in the transcript is caught before the Consumer
 * is asked to approve it.
 */
export type SeenCall = {
	name: string;
	/** Which shop answered. Every recorded call has one: a catalogue fact with
	 *  no shop attached is a fact about no particular shop. */
	shop: string;
	args: Record<string, unknown>;
	result: unknown;
};

/** One tool a `'generic'` shop's own `tools/list` named. Enough to build its
 *  schema for the model and classify its consent tier — never the OpenStore
 *  closed set's own shape, which is why this is its own small type rather
 *  than reusing `mcp/client.ts`'s `ToolDescriptor` (`loop.ts` reasons about
 *  tools; it does not fetch them). */
export type GenericTool = {
	name: string;
	description: string;
	inputSchema: Record<string, unknown>;
	annotations: { readOnlyHint?: boolean; destructiveHint?: boolean; moneyPathHint?: boolean };
};

/** A shop this chat has been introduced to: its domain is the address, its
 *  name is what the Consumer calls it. `kind` and `tools` are absent (or
 *  `'openstore'`/undefined) for one of this repo's own shops — the closed
 *  14-tool set applies unconditionally, the way it always has. A `'generic'`
 *  shop carries its own `tools/list` answer, cached at add time, because
 *  that — not `TOOLS` — is the only true source for what it offers. */
export type Shop = {
	domain: string;
	name: string;
	/** What the shop's own card says it sells. Declared by that Merchant and
	 *  verified by nobody, which is exactly how it is used: to decide who to
	 *  ask FIRST, never to decide a shop has nothing. */
	description?: string;
	category?: string;
	kind?: 'openstore' | 'generic';
	tools?: GenericTool[];
};

/**
 * The shops this chat holds, as a line each, for the model's context.
 *
 * The model previously learned a shop existed only by seeing its domain come
 * back in a result — so the only way to find out who stocks mugs was to ask all
 * ten. Given the roster up front it can address the likely one or two by name,
 * and the fan-out becomes the fallback it was meant to be rather than the
 * opening move.
 *
 * **Framed as a hint on purpose.** These descriptions are the Merchants' own
 * words about themselves; the card says as much in its `note`. Four SKUs in
 * this demo deliberately sit in two shops at once, so a roster read as a filter
 * gets a confident "we don't stock that" about stock that is right there.
 */
export function shopRoster(shops: readonly Shop[]): string {
	if (!shops.length) return 'No shops have been added yet.';
	const lines = shops.map((shop) => {
		const about = [shop.description, shop.category].filter(Boolean).join(' · ');
		return `- ${shop.name} (${shop.domain})${about ? ` — ${about}` : ''}`;
	});
	return (
		`The shopper has added ${shops.length} shop${shops.length === 1 ? '' : 's'}:\n` +
		`${lines.join('\n')}\n\n` +
		'Each shop wrote its own line above and nobody checked it. Use them to pick ' +
		'which shop to ask first — put its domain in `"shop"` — and never to decide a ' +
		'shop does not stock something. Shops here carry overlapping stock, and the ' +
		'only way to establish that a shop lacks an item is to search it. If the ' +
		'likely shops come back empty, widen to the rest before telling the shopper ' +
		'it cannot be found.'
	);
}

function genericTool(shops: readonly Shop[], name: string): { shop: Shop; tool: GenericTool } | null {
	for (const shop of shops) {
		const tool = shop.tools?.find((t) => t.name === name);
		if (tool) return { shop, tool };
	}
	return null;
}

/** Every tool name reachable right now: the closed set, plus whatever each
 *  connected generic shop's own `tools/list` named. The one gate everything
 *  else — dispatch, the model's own schema, consent — is built from. */
export function reachableTools(shops: readonly Shop[]): Set<string> {
	const names = new Set<string>(TOOLS);
	for (const shop of shops) for (const tool of shop.tools ?? []) names.add(tool.name);
	return names;
}

/** The two scopes that ever move toward a spend, generalised past the
 *  closed set: a generic tool is money-path when its own server says so
 *  (`moneyPathHint`/`destructiveHint`), and — since nothing here can verify
 *  a server that stays silent — money-path by default when it says nothing
 *  at all. The closed set never needed a default; every one of its tools
 *  already declares a scope. */
export function isMoneyPath(name: string, shops: readonly Shop[]): boolean {
	if ((TOOLS as readonly string[]).includes(name)) {
		const scope = SCOPES[name as ToolName];
		return scope === 'start-checkout' || scope === 'confirm';
	}
	const found = genericTool(shops, name);
	if (!found) return true;
	const { moneyPathHint, destructiveHint, readOnlyHint } = found.tool.annotations;
	if (moneyPathHint || destructiveHint) return true;
	// readOnlyHint is the one positive safety signal worth trusting on its
	// own — a tool that says it reads and changes nothing is exactly the
	// case ALWAYS_ALLOWABLE already treats as safe for the closed set. A
	// tool that says neither gets the same default an unrecognised one
	// does: nothing here can verify silence, so silence is money-path.
	return !readOnlyHint;
}

/** `description`/`inputSchema` for the model's native tool-calling, merged
 *  across the closed set and every connected generic shop. A generic tool's
 *  own JSON Schema is used verbatim — it is real JSON Schema (ADR-0026 on
 *  the sidecar side; whatever the generic server itself declares here),
 *  unlike the closed set's schemas, which predate that and are converted by
 *  `TOOL_SCHEMAS`' own shape below. */
export function schemasFor(
	shops: readonly Shop[]
): Record<string, { description: string; parameters: object }> {
	const merged: Record<string, { description: string; parameters: object }> = { ...TOOL_SCHEMAS };
	for (const shop of shops) {
		for (const tool of shop.tools ?? []) {
			if (merged[tool.name]) continue; // the closed set wins a name collision
			merged[tool.name] = { description: tool.description, parameters: tool.inputSchema };
		}
	}
	return merged;
}

export type SeenVariant = {
	group: string;
	/** The shop this variant was read at. A SKU is only a SKU somewhere. */
	shop: string;
	name: string;
	/** The option values that distinguish this variant: `{size: "M"}`. */
	options: Record<string, string>;
	/** How many variants the group has. One means there is nothing to choose. */
	siblings: number;
	addon: boolean;
};

export type Seen = {
	groups: ReadonlySet<string>;
	addons: ReadonlySet<string>;
	variants: ReadonlyMap<string, SeenVariant>;
	/** Group ids known to have option axes, from `search` — enough to know a
	 *  question exists before `read-item` has named the variants. */
	axes: ReadonlyMap<string, string[]>;
	/** The lines each shop last reported, by domain. One basket per shop,
	 *  because one sidecar per shop is what the protocol says (ADR-0007) —
	 *  merging them here would invent a basket no shop can quote. */
	baskets: ReadonlyMap<string, ReadonlySet<string>>;
	/** Which shops a SKU or group id has been seen at. Usually one, which is
	 *  what lets a call name no shop and still reach the right one. */
	sources: ReadonlyMap<string, ReadonlySet<string>>;
};

/** Arguments as a comparable string. Key order is whatever the model happened
 *  to emit, so `{sku, qty}` and `{qty, sku}` are the same question and must
 *  compare equal. */
function canonicalArgs(args: Record<string, unknown> | undefined): string {
	const entries = Object.entries(args ?? {}).sort(([a], [b]) => (a < b ? -1 : 1));
	return JSON.stringify(entries);
}

/**
 * Has this exact read already been asked of this exact shop and answered, with
 * nothing written since?
 *
 * Catalogue reads are idempotent: the same question to the same shop with no
 * write in between returns the same bytes, and the model already holds them
 * verbatim in its own context. Models re-read anyway — they re-derive rather
 * than look back — and every repeat is a round trip the shopper waits through
 * and a card they watch scroll past.
 *
 * **Bounded by the last write, deliberately.** A write can move stock, empty a
 * basket or invalidate a quote, so a read after one is a genuinely new question
 * and is asked again. This only removes work that provably cannot have a
 * different answer.
 *
 * `order-status` is never skipped: an order's fate moves on the shop's side
 * without this chat doing anything, so "nothing written here" says nothing
 * about whether the answer changed.
 */
export function answeredAlready(
	calls: readonly SeenCall[],
	domain: string,
	call: ToolCall
): boolean {
	if (call.name === 'order-status') return false;
	let from = 0;
	for (let i = calls.length - 1; i >= 0; i -= 1) {
		if (!READS.has(calls[i]!.name)) {
			from = i + 1;
			break;
		}
	}
	const wanted = canonicalArgs(call.args);
	return calls.slice(from).some(
		(prior) =>
			prior.shop === domain &&
			prior.name === call.name &&
			canonicalArgs(prior.args) === wanted &&
			prior.result !== null &&
			prior.result !== undefined &&
			// A refusal is not an answer worth reusing: a shop that was rate
			// limited or briefly unreachable should be asked again.
			!(prior.result as Record<string, unknown>).error
	);
}

export function catalogueFrom(calls: readonly SeenCall[]): Seen {
	const groups = new Set<string>();
	const addons = new Set<string>();
	const variants = new Map<string, SeenVariant>();
	const axes = new Map<string, string[]>();
	const baskets = new Map<string, ReadonlySet<string>>();
	const sources = new Map<string, Set<string>>();
	const from = (id: string, shop: string) => {
		if (!shop) return;
		const at = sources.get(id) ?? new Set<string>();
		at.add(shop);
		sources.set(id, at);
	};
	for (const call of calls) {
		const result = (call.result ?? {}) as Record<string, any>;
		for (const row of (result.results ?? []) as any[]) {
			if (typeof row?.group === 'string') {
				groups.add(row.group);
				from(row.group, call.shop);
			}
			const named = Object.keys(row?.option_axes ?? {});
			if (named.length) axes.set(String(row.group), named);
		}
		if (typeof result.group === 'string' && Array.isArray(result.variants)) {
			groups.add(result.group);
			from(result.group, call.shop);
			const named = Object.keys(result.option_axes ?? {});
			if (named.length) axes.set(result.group, named);
			for (const variant of result.variants as any[]) {
				if (typeof variant?.sku !== 'string') continue;
				const addon = ((variant.tags ?? []) as string[]).includes('addon');
				if (addon) addons.add(variant.sku);
				from(variant.sku, call.shop);
				variants.set(variant.sku, {
					group: result.group,
					shop: call.shop,
					name: typeof variant.name === 'string' ? variant.name : variant.sku,
					options: (variant.options ?? {}) as Record<string, string>,
					siblings: (result.variants as any[]).length,
					addon
				});
			}
		}
		// Each shop's own last word on its basket, kept apart by shop.
		if (Array.isArray(result.lines)) {
			baskets.set(call.shop, new Set((result.lines as any[]).map((line) => String(line.sku))));
		}
	}
	return { groups, addons, variants, axes, baskets, sources };
}

/** Which shops a call could be meant for, in the order preference runs:
 *  what it named, what it can only mean, and — for a read — everywhere. */
export function shopsFor(
	call: ToolCall,
	seen: Seen,
	shops: readonly Shop[]
): string[] {
	const known = new Set(shops.map((shop) => shop.domain));
	const named = typeof call.args?.shop === 'string' ? call.args.shop : '';
	if (named) {
		if (!known.has(named)) {
			throw new ToolError(
				'unknown-shop',
				`I have not been introduced to ${named}. Add it on the Shops page first.`
			);
		}
		return [named];
	}
	if (shops.length === 1) return [shops[0]!.domain];

	// A tool outside the closed set belongs to whichever generic shop's own
	// tools/list named it — never inferred from sku/group the way the closed
	// set's writes are, since a generic tool's arguments follow that server's
	// own schema and mean nothing here.
	if (!(TOOLS as readonly string[]).includes(call.name)) {
		const declaring = shops.filter((shop) => shop.tools?.some((t) => t.name === call.name));
		if (declaring.length === 1) return [declaring[0]!.domain];
		if (declaring.length > 1) {
			throw new ToolError(
				'shop-required',
				`More than one shop offers ${call.name}. Which one did you mean?`,
				{ candidates: declaring }
			);
		}
		throw new ToolError('unknown-tool', `${call.name} is not a tool any known shop offers.`);
	}

	// The id a call names usually says where it goes: an id seen at one shop
	// and nowhere else can only mean that shop. Consulted for reads as well as
	// writes, and BEFORE the fan-out below — a `read-item` for a group the
	// transcript already located at Kettle & Grain was being asked of every
	// shop, so two thirds of the cards a Consumer watched go past were
	// `not-found` for a question nobody had.
	const ids = [call.args?.sku, call.args?.group, call.args?.parent, call.args?.order_id]
		.filter((value): value is string => typeof value === 'string' && value.length > 0);
	const candidates = new Set<string>();
	for (const id of ids) {
		for (const shop of seen.sources.get(id) ?? []) candidates.add(shop);
	}
	if (candidates.size === 1) return [...candidates];

	// A read whose id is genuinely at more than one shop asks exactly those —
	// the overlapping SKUs are the case this exists for, and asking the two
	// that stock it still beats asking all ten.
	if (READS.has(call.name) && candidates.size > 1) return [...candidates];

	// A read that names nothing to resolve by goes to every shop: "find me a
	// notebook" is a question about the shops the Consumer has, not about
	// whichever one was added last. Reads change nothing, so asking all of
	// them costs only the round trips — which run concurrently.
	if (READS.has(call.name) && !call.args?.order_id) {
		return shops.map((shop) => shop.domain);
	}

	// No id at all names a shop — `set-destination`, `set-contact`,
	// `start-checkout` and `place-order` never carry one. These are the steps
	// that *continue* a checkout already under way, so the shop with lines in
	// its basket is a stronger signal than "ask again": a shopper mid-checkout
	// at one shop should not be re-asked which shop they meant on every step.
	// Silent only because it is unambiguous — two shops both holding lines
	// falls through to asking, same as an outright collision.
	if (candidates.size === 0) {
		const withBasket = shops.filter((shop) => (seen.baskets.get(shop.domain)?.size ?? 0) > 0);
		if (withBasket.length === 1) return [withBasket[0]!.domain];
	}

	// Two or more shops sell the same-named id (the ordinary case for a
	// deliberately overlapping SKU, e.g. two shops both selling AA batteries)
	// — or the id is unrecognised anywhere and every shop is offered as a
	// starting point. Either way the fix is the same tap; only the copy
	// differs.
	const pick = candidates.size > 1 ? candidates : new Set(shops.map((shop) => shop.domain));
	throw new ToolError(
		'shop-required',
		candidates.size > 1
			? `More than one shop sells that. Which one did you mean?`
			: `You have ${shops.length} shops and this step has to happen at one of them. Which shop?`,
		{ candidates: shops.filter((shop) => pick.has(shop.domain)) }
	);
}

/** The Consumer's words as whole tokens. Substring matching cannot be used for
 *  an option value: every sentence with an `m` in it contains the size M. */
function tokens(said: string): Set<string> {
	return new Set(said.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean));
}

/**
 * A variant the Consumer never chose is refused **before it is proposed**.
 *
 * "I want a cap" is not a choice of size. Asked for one anyway, a model picks
 * — and it picked the low-stock M over the in-stock S, under a group badge
 * that read in-stock, with a consent prompt that showed `{"sku":"SD-CAP-M"}`
 * as though the Consumer had said it. The shop cannot catch this: a SKU is a
 * SKU, and `variant-required` there only refuses a *group* id.
 *
 * Naming the SKU counts, and so does naming the option value on every axis —
 * including by tapping a `choices` widget, whose label is recorded as the
 * Consumer's own words.
 */
export function assertVariantChosenByConsumer(sku: string, seen: Seen, said: string): void {
	const variant = seen.variants.get(sku);
	// Nothing read about it: the shop is the authority, not a guess here.
	if (!variant || variant.siblings < 2) return;
	const words = tokens(said);
	if (normalise(said).includes(normalise(sku))) return;
	const values = Object.values(variant.options).filter((v) => typeof v === 'string' && v.trim());
	if (values.length && values.every((value) => words.has(value.toLowerCase()))) return;
	throw new ToolError(
		'variant-required',
		`${variant.group} comes in ${variant.siblings} options and you have not picked one yet.`
	);
}

export function validate(call: ToolCall, shops: readonly Shop[] = []): ToolCall {
	if (!reachableTools(shops).has(call.name)) {
		throw new ToolError('unknown-tool', `${call.name} is not a tool this agent has.`);
	}
	// Everything past here is the closed set's own shape (a SKU that
	// resolves, a quantity that is a whole number, ...). A generic tool's
	// arguments follow that server's own JSON Schema instead, checked
	// server-side when the call actually reaches it — this function has no
	// second schema to check them against, and inventing one would be
	// exactly the kind of guess this repo's tools never make about a
	// Merchant's own data.
	if (!(TOOLS as readonly string[]).includes(call.name)) return call;
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
		description:
			"Search this shop's catalogue by words the shopper used. Call with an empty " +
			'query to browse the full catalogue in one go — do that first when the shopper ' +
			'asks for ideas, gifts, or an occasion rather than a named product.',
		parameters: {
			type: 'object',
			properties: { query: { type: 'string' } },
			required: []
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
	'clear-basket': {
		description:
			'Empty the basket back to a fresh state: lines, destination, contact, ' +
			'fulfillment choice and code all go. Only for starting over, and only ' +
			'when the shopper asks to or accepts it.',
		parameters: { type: 'object', properties: {} }
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
export const SYSTEM_PROMPT = `You are a shopping agent. The shopper may have introduced you to more than one shop; you talk to all of them through the same tools.

Hard rules, and they are not style preferences:
- A search or a read with no shop named goes to every shop the shopper has
  added, and results say which shop each one came from. If the shopper
  clearly means one shop ("at CircuitYard", "the bookshop"), or two shops both
  answer to the same id, include a \`"shop"\` field in that call's arguments
  naming the shop's domain — you will see the domain in each result.
- You NEVER state a price, total, tax, discount or stock count that did not come back from a tool. If you do not have it, call a tool or say you do not know.
- You NEVER compute or estimate a total. The shop's own quote is the only total.
- You CANNOT pay. \`place-order\` returns a link the shopper approves on the shop's own page; you hold no payment credential. Say so plainly rather than implying you can buy.
- You NEVER invent a delivery address, an email or a phone number. Ask for them.
- A SKU is what can be bought; a group cannot. Resolve options first with read-item.
- You NEVER pick an option the shopper did not name. A group with two sizes and
  a shopper who said "a cap" is a question, not a choice you make for them —
  offer the variants as a \`choices\` widget. Proposing one anyway is refused
  before the shopper is asked about it, and the search result's \`option_axes\`
  already tells you whether a question exists.
- The basket holds what the shopper asked for or agreed to, and nothing else.
  Read the basket the tool hands back after every call: a line you cannot trace
  to this conversation is one to remove, not to quote.
- Only do what the shopper asked for or accepted. Never add, remove, or pick
  anything unprompted — propose it in words and wait for a yes. If a tool
  result shows basket lines this conversation did not add, stop and ask before
  touching anything; offer to start over with a fresh basket.
- Build the cart completely first. Only then resolve the destination, then the
  fulfillment option, then the quote — in that order. Changing the basket
  un-chooses delivery, so an option picked before the last item went in has to
  be picked again.
- \`choose-fulfillment\` with no id asks what is offered; it chooses nothing.
  Only a call carrying an id chooses, and only its answer means delivery is set.
- Nothing is ordered or paid until the shopper approves the exact amount on the
  shop's own page. Never say or imply otherwise, and never call a step done
  that a tool has not done.
- If you have no destination, ask the shopper for it.
  Never invent one, not even a placeholder — the shop is sent exactly the
  address the shopper typed, and anything else is refused before it is asked
  about.
- When the shopper asks for ideas — a birthday gift, an occasion, "something
  for…" — browse the full catalogue first (search with an empty query), then
  recommend across groups using your judgment of what fits. Never recommend a
  product you have not seen in a tool result.
- When a tool refuses, tell the shopper the shop's reason in plain words. Do not retry blindly.

Work you have already done is in front of you. Every tool call in this
conversation is shown with its full result, so before you call anything, look
back:
- If a previous result already carries the SKU, options, price or stock you
  need, use it. Re-reading the same group returns the same bytes and the
  shopper waits through it for nothing.
- Once you know which shop holds something, put its domain in \`"shop"\` on
  every later call about it. A read with no shop named asks every shop, and the
  ones that do not stock it answer \`not-found\` in front of the shopper.
- \`search\` with an empty query returns a shop's whole catalogue. Once you have
  it, browse it from the transcript — do not search again for each new idea.
- Ask for everything you need in one turn. Independent reads in a single turn
  are sent together and come back together; the same reads spread over four
  turns are four waits.
- A tool result you can quote beats a tool call you can make. The shortest path
  to an answer the shopper can act on is the right one.

Be warm and conversational, like a good shop assistant: greet, acknowledge,
and sound glad to help. Keep replies short — one or two sentences — and stay
plain around money steps: never dress up a total, a refusal, or an approval
link.

WIDGETS. When the shopper's next step is a choice or some details, end your
message with one fenced \`widget\` block and let them tap or type instead of
composing a sentence. The block is the last thing in the message, and there is
never more than one. Ask for exactly what *this* shop needs — a shop that
notifies by phone gets a phone field, one that emails gets an email field, one
that wants both gets both — and write the fields from the shop's own tool
results, never from habit. Amounts never go in a widget; the costs render
themselves beside it. Widgets cannot check out or place an order: those stay
sentences, and the shopper approves the amount on the shop's own page.

Pick the options — a size, a delivery id:

\`\`\`widget
{"kind":"choices","title":"Which tote?","tool":"add-line","arg":"sku","args":{"qty":1},
 "options":[{"label":"Red / L","value":"SD-TOTE-RED-L"},{"label":"Red / M","value":"SD-TOTE-RED-M"}]}
\`\`\`

Ask for details, shaped the way the shop's schema nests them:

\`\`\`widget
{"kind":"form","title":"Delivery address","submit":"Use this address","tool":"set-destination",
 "group":"destination","fields":[
  {"name":"line1","label":"Street address","kind":"text","required":true},
  {"name":"city","label":"City","kind":"text","required":true},
  {"name":"state","label":"State code","kind":"text","required":true,"placeholder":"KA"},
  {"name":"postal_code","label":"PIN code","kind":"text","required":true}]}
\`\`\`

\`\`\`widget
{"kind":"form","title":"Where should the shop send updates?","submit":"Save","tool":"set-contact",
 "group":"contact","fields":[{"name":"phone","label":"Phone","kind":"tel","required":true}]}
\`\`\`

Offer replies when the next move is just a word:

\`\`\`widget
{"kind":"chips","options":["Yes, add it","Show me something else"]}
\`\`\``;
