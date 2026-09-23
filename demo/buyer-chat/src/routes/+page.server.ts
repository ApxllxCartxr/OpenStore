/**
 * The chat itself: a Consumer types, the agent proposes tool calls, and every
 * call reaches a real shop.
 *
 * This page had no form and no actions — it rendered one canned greeting and a
 * consent dialog whose buttons cleared local state. The agent "computed
 * nothing" because there was no loop at all.
 *
 * **The agent still computes nothing, and that is the point.** Every price,
 * total, availability and refusal on this page is a value the shop returned.
 * The loop proposes calls, deterministic code makes them, and what comes back
 * is rendered verbatim.
 */
import { fail } from '@sveltejs/kit';
import {
	THREAD,
	addMessage,
	clearSetting,
	createThread,
	getSetting,
	recordToolCall,
	setSetting,
	thread,
	widgetFor
} from '$lib/session.ts';
import {
	availableDrivers,
	driverFor,
	isOffer,
	OpenRouterDriver,
	type Proposal,
	type ToolCall,
	type Turn
} from '$lib/model/driver.ts';
import {
	ALWAYS_ALLOWABLE,
	MAX_STEPS,
	READS,
	TOOLS,
	ToolError,
	answeredAlready,
	assertAddonHasParent,
	assertDestinationFromConsumer,
	assertResolvedVariant,
	assertVariantChosenByConsumer,
	catalogueFrom,
	consentNote,
	hasMeaningfulArgs,
	isMoneyPath,
	reachableTools,
	requiresFreshConsent,
	schemasFor,
	shopRoster,
	shopsFor,
	validate,
	type Seen,
	type Shop,
	type ToolName
} from '$lib/tools/loop.ts';
import { call as callShop, callGeneric, ShopError } from '$lib/mcp/client.ts';
import { explain } from '$lib/quote.ts';
import { argsFor, composeMessage, parseWidget, saidBySubmitting, widgetForRefusal } from '$lib/widgets.ts';
import { db } from '$lib/session.ts';

/** Every shop this chat has been introduced to. A chat with none can still
 *  be typed into — it just says so. Order is oldest-added first, which is
 *  only ever used as a display order; dispatch never picks "the" shop by
 *  position (`shopsFor`, below the tool loop, resolves by what a call named
 *  or can only mean). */
function knownShops(): Shop[] {
	const rows = db
		.prepare(
			`SELECT domain, name, description, category, kind, mcp_endpoint, tools
			   FROM contacts ORDER BY added_at ASC`
		)
		.all() as {
		domain: string;
		name: string;
		description: string;
		category: string;
		kind: string;
		mcp_endpoint: string;
		tools: string;
	}[];
	return rows.map((row) => ({
		domain: row.domain,
		name: row.name,
		description: row.description,
		category: row.category,
		kind: row.kind === 'generic' ? 'generic' : 'openstore',
		tools: row.kind === 'generic' ? (JSON.parse(row.tools) as Shop['tools']) : undefined
	}));
}

/** The endpoint a generic shop's tools/call actually goes to — its own URL,
 *  not a domain to dial the way an openstore shop's own agent/mcp is. */
function genericEndpoint(domain: string): string {
	const row = db.prepare(`SELECT mcp_endpoint FROM contacts WHERE domain = ?`).get(domain) as
		| { mcp_endpoint: string }
		| undefined;
	return row?.mcp_endpoint ?? '';
}

function shopName(domain: string, shops: readonly Shop[]): string {
	return shops.find((shop) => shop.domain === domain)?.name ?? domain;
}

const DRIVER_SETTING = 'model_driver_override';

/** The Consumer's own switch, when it names a driver this process actually
 *  has a key or a model for; the operator's deploy-time default otherwise —
 *  `driverFor(null)` is exactly `buildDriver`'s old always-the-env-var
 *  behaviour. */
function currentDriver() {
	return driverFor(getSetting(DRIVER_SETTING));
}

/** Resolve a call to the shop(s) it targets, without throwing: `shopsFor`'s
 *  `shop-required` is a question for the Consumer, not a bug, so every call
 *  site handles it the same way rather than repeating a try/catch. */
function resolve(call: ToolCall, seen: Seen, shops: readonly Shop[]): string[] | ToolError {
	try {
		return shopsFor(call, seen, shops);
	} catch (error) {
		return error as ToolError;
	}
}

/** Standing permission is per thread and per tool, and it never covers a spend
 *  step — `ALWAYS_ALLOWABLE` is the whole of what it can hold (ADR-0008). */
const standing = new Set<string>();
/** A proposed call waiting on the Consumer, with the shop it was resolved to
 *  — resolved once, when the call was proposed, so the tap the Consumer
 *  answers is the shop they were shown. One at a time, deliberately: a queue
 *  of consent prompts is how people click through them. */
let awaiting: { call: ToolCall; domain: string } | null = null;

export async function load() {
	createThread(THREAD, 'SpoiledDuckie');
	const state = thread(THREAD);
	const shops = knownShops();
	if (!state.messages.length && !state.toolCalls.length) {
		await startFresh(shops);
		addMessage(
			THREAD,
			'agent',
			(shops.length > 1
				? `Hey, I’m Miro! Tell me what you’re looking for and I’ll search across your ${shops.length} shops. `
				: 'Hey, I’m Miro! Tell me what you’re looking for and I’ll search the shop. ') +
				'I can browse and build a basket — I can’t spend. Every purchase ends with you ' +
				'approving the exact amount on the shop’s own page.'
		);
	}
	return {
		thread: thread(THREAD),
		driver: currentDriver().name,
		drivers: availableDrivers(),
		driverChoice: getSetting(DRIVER_SETTING),
		tools: [...reachableTools(shops)],
		shops,
		pending: awaiting
			? {
					name: awaiting.call.name,
					args: awaiting.call.args,
					domain: awaiting.domain,
					shopName: shopName(awaiting.domain, shops),
					overCeiling: pendingOverCeiling(awaiting)
				}
			: null,
		pendingNote: awaiting ? consentNote(awaiting.call) : null,
		pendingHasArgs: awaiting ? hasMeaningfulArgs(awaiting.call.args) : false,
		standing: [...standing]
	};
}

/**
 * The advisory ceiling, checked against the same shop's most recent quote —
 * `place-order` itself carries no total, so this is what the pending call was
 * proposed alongside, not a number this chat computed. Null unless there is
 * both a ceiling set and a quote to compare it against; the modal falls back
 * to its ordinary copy either way.
 */
function pendingOverCeiling(pending: { call: ToolCall; domain: string }): number | null {
	if (pending.call.name !== 'place-order') return null;
	const ceiling = getSetting('spend_ceiling_minor');
	if (!ceiling) return null;
	const ceilingMinor = Number(ceiling);
	const calls = recordedCalls();
	for (let i = calls.length - 1; i >= 0; i -= 1) {
		const call = calls[i]!;
		if (call.shop !== pending.domain || call.name !== 'start-checkout') continue;
		const total = (call.result as Record<string, any> | null)?.total_minor;
		if (typeof total === 'number' && total > ceilingMinor) return total;
		return null;
	}
	return null;
}

/**
 * A new conversation starts on an empty basket.
 *
 * The basket lives in the shop, keyed by this chat's agent identity — which is
 * stable for the life of the install. So it outlived the conversation that
 * filled it: a fresh thread's first `add-line` came back holding the previous
 * shopper's lines, an address nobody here had given, and a delivery option
 * chosen for neither. Every one of those lines broke the rule that a basket
 * holds what *this* Consumer asked for. Cleared here, where the conversation
 * demonstrably begins, rather than left to the model to remember.
 *
 * Best effort: a shop that cannot be reached leaves the basket alone and says
 * so in the tool card. It is not a reason to refuse to open the chat.
 */
async function startFresh(shops: readonly Shop[]): Promise<void> {
	await Promise.all(
		// A generic shop has no basket concept at all — clear-basket is this
		// repo's own tool, not a thing every MCP server is assumed to have.
		shops
			.filter((shop) => shop.kind !== 'generic')
			.map(async (shop) => {
				try {
					const result = await callShop(shop.domain, 'clear-basket', {});
					// Recorded only when it did something: an empty clear on a fresh
					// thread is noise, but removed lines are exactly what the shopper
					// must see.
					if (((result.removed ?? []) as string[]).length) {
						recordToolCall(THREAD, shop.domain, 'clear-basket', {}, result);
					}
				} catch (error) {
					const refusal = error as ShopError;
					recordToolCall(THREAD, shop.domain, 'clear-basket', {}, {
						error: refusal.code,
						detail: refusal.message
					});
				}
			})
	);
}

function describe(tool: string, result: Record<string, any>, previousNeeds: string | null = null): string {
	/** What to say about a result. Never a number this file invented. */
	if (tool === 'search') {
		const results = (result.results ?? []) as any[];
		if (!results.length) return 'Nothing in the shop matches that — want to try different words?';
		return `Found ${results.length} ${results.length === 1 ? 'group' : 'groups'} — here they are:`;
	}
	if (tool === 'read-item') {
		return `${result.name} comes in ${(result.variants ?? []).length} variants — tell me which options you’d like.`;
	}
	if (tool === 'choose-fulfillment' && Array.isArray(result.options)) {
		// Listing mode (no id yet): the shop named its options, nothing is
		// chosen. Saying anything else here would pretend otherwise.
		const options = result.options as { label: string; cost_minor: number; eta_days: number }[];
		if (!options.length) return 'The shop offers no delivery options for that address yet.';
		const named = options.map(
			(o) => `${o.label} — ${formatMinor(Number(o.cost_minor))}, ${Number(o.eta_days)} days`
		);
		return `The shop offers: ${named.join('; ')}. Which one should I pick?`;
	}
	if (tool === 'clear-basket') {
		const removed = (result.removed ?? []) as string[];
		return removed.length
			? `Cleared the basket — removed ${removed.join(', ')}. Starting fresh.`
			: 'The basket is already empty. Starting fresh.';
	}
	if (tool === 'start-checkout') {
		return `The shop has quoted this order — the exact total is below, and approving it is yours to do, not mine.`;
	}
	if (tool === 'place-order') {
		return 'Here’s your approval link! You approve the exact amount on the shop’s own page — that part is all you.';
	}
	if (result.total_minor !== undefined && result.total_minor !== null) {
		return `Your basket comes to ${formatMinor(Number(result.total_minor))}, per the shop.`;
	}
	if (result.needs) {
		// The same "still needed" line after every add-line is noise the
		// Consumer learns to skip — said once, and again only when the list
		// actually changes.
		const needs = (result.needs as string[]).join(', ');
		return needs === previousNeeds ? 'Basket updated!' : `Basket updated! Still needed: ${needs}.`;
	}
	return 'Done!';
}

/** What the shop last said was missing, before this call. Read from the
 *  transcript so the comparison survives a restart. */
function previousNeedsOf(calls: readonly { result: unknown }[]): string | null {
	for (let i = calls.length - 1; i >= 0; i -= 1) {
		const needs = (calls[i]?.result as Record<string, any> | null)?.needs;
		if (Array.isArray(needs)) return needs.join(', ');
	}
	return null;
}

function formatMinor(minor: number): string {
	// Display only — the shop's own integer, never arithmetic on a price.
	return `₹${(minor / 100).toFixed(2)}`;
}

/** A refusal the Consumer can act on: the shop's own detail when it carries
 *  one, the reason-code copy otherwise. Never `code — raw`, which is how a
 *  validation dump once reached a shopper. The code itself stays in the tool
 *  card's JSON for debugging. */
function refusalText(refusal: ShopError): string {
	const detail = refusal.message.trim();
	return detail ? detail : explain(refusal.code);
}

/** `args.shop` is routing, read by `shopsFor`/`resolve` to pick a domain —
 *  never a real tool argument, and a shop already dispatched to has no more
 *  use for it. Stripped before the call is sent or recorded, so the tool
 *  card shows only what the tool actually needed. */
function withoutShopArg(args: Record<string, unknown>): Record<string, unknown> {
	if (!('shop' in args)) return args;
	const { shop: _shop, ...rest } = args;
	return rest;
}

/** One shop, one call, whichever transport it actually needs — self-registered
 *  bearer auth against this repo's own shops, no auth against anything else
 *  (`mcp/client.ts`'s own header). The rest of this file never branches on
 *  kind again once a call has been dispatched through here. */
async function dispatch(domain: string, shops: readonly Shop[], tool: string, args: Record<string, unknown>) {
	const shop = shops.find((s) => s.domain === domain);
	if (shop?.kind === 'generic') return callGeneric(genericEndpoint(domain), tool, args);
	return callShop(domain, tool, args);
}

async function runCall(
	domain: string,
	shops: readonly Shop[],
	call: ToolCall
): Promise<Record<string, any>> {
	const args = withoutShopArg(call.args);
	const result = await dispatch(domain, shops, call.name, args);
	recordToolCall(THREAD, domain, call.name, args, result);
	return result;
}

/** Reads, concurrently, fanned out across every shop each one resolves to.
 *  An unaddressed search reaches every shop the Consumer has; one naming a
 *  shop, or an order-status call, reaches exactly one. Recorded in the order
 *  the model planned them rather than the order they came back, so the
 *  transcript reads as one decision. */
async function runReads(reads: readonly ToolCall[], seen: Seen, shops: readonly Shop[]): Promise<void> {
	const jobs = reads.flatMap((read) => {
		const domains = resolve(read, seen, shops);
		// A read cannot actually go unresolved — `shopsFor` fans an unnamed one
		// out to every shop — but a shop named that this chat has never heard
		// of still refuses, and that refusal belongs in the transcript like any
		// other.
		if (domains instanceof ToolError) {
			recordToolCall(THREAD, '', read.name, read.args, { error: domains.code, detail: domains.message });
			return [];
		}
		return domains
			.map((domain) => ({ call: { ...read, args: withoutShopArg(read.args) }, domain }))
			// Already asked, nothing written since, answer still in the model's
			// own context. Dropped silently: a card saying "asked again, same
			// answer" is the noise this exists to remove.
			.filter((job) => !answeredAlready(recordedCalls(), job.domain, job.call));
	});
	const settled = await Promise.allSettled(
		jobs.map((job) => dispatch(job.domain, shops, job.call.name, job.call.args))
	);
	settled.forEach((outcome, n) => {
		const { call, domain } = jobs[n]!;
		if (outcome.status === 'fulfilled') {
			recordToolCall(THREAD, domain, call.name, call.args, outcome.value);
			return;
		}
		const refusal = outcome.reason as ShopError;
		recordToolCall(THREAD, domain, call.name, call.args, { error: refusal.code, detail: refusal.message });
	});
}

/** Run a call and say what the shop said back. One place, because the `needs`
 *  comparison has to be taken *before* the new result is recorded. */
async function runAndSay(domain: string, shops: readonly Shop[], call: ToolCall): Promise<void> {
	const before = previousNeedsOf(recordedCalls());
	const result = await runCall(domain, shops, call);
	addMessage(THREAD, 'agent', describe(call.name, result, before));
}

/** Has the agent actually said anything since the Consumer last spoke?
 *
 *  Reasoning-only rows carry empty text and do not count — they are a thinking
 *  disclosure, not an answer. Neither do the tool cards: a Consumer who asked a
 *  question and got only cards is owed a sentence. This is what decides whether
 *  an empty completion needs covering or is simply the model having nothing to
 *  add to a line the app already wrote. */
function hasSpokenSince(): boolean {
	const messages = thread(THREAD).messages;
	let last = -1;
	for (let i = messages.length - 1; i >= 0; i -= 1) {
		if (messages[i]?.role === 'consumer') {
			last = i;
			break;
		}
	}
	return messages.slice(last + 1).some((m) => m.role === 'agent' && m.text.trim().length > 0);
}

/** The tool calls of this thread, parsed — the transcript is the only record
 *  of the shop's catalogue this agent is allowed to reason from. */
function recordedCalls() {
	return thread(THREAD).toolCalls.map((c) => ({
		shop: c.shop,
		name: c.name,
		args: JSON.parse(c.request) as Record<string, unknown>,
		result: c.response ? JSON.parse(c.response) : null
	}));
}

function seenCatalogue(): Seen {
	return catalogueFrom(recordedCalls());
}

/** Everything the Consumer has typed in this thread, which is the only source
 *  of an address, an email or a phone this agent has. */
function consumerSaid(): string {
	return thread(THREAD)
		.messages.filter((m) => m.role === 'consumer')
		.map((m) => m.text)
		.join(' ');
}

/**
 * Deterministic checks on a *proposal*, before the Consumer is asked about it.
 *
 * Returns the refusal to record and tell the model about, or null to proceed.
 * The consent prompt shows the exact JSON of whatever gets this far, so an
 * invented address must never reach it — approving one would look, to the
 * Consumer, exactly like approving their own.
 */
function proposalRefusal(shop: string, call: ToolCall, shops: readonly Shop[]): ToolError | null {
	try {
		// Shape first: a malformed call is refused here rather than spent as a
		// round trip to a shop that would refuse the same thing.
		validate(call, shops);
		if (call.name === 'add-line') {
			const seen = seenCatalogue();
			const sku = String(call.args?.sku ?? '');
			assertResolvedVariant(sku, seen.groups);
			assertAddonHasParent(sku, call.args?.parent, seen.addons, seen.baskets.get(shop) ?? new Set());
			assertVariantChosenByConsumer(sku, seen, consumerSaid());
		}
		if (call.name === 'set-destination') {
			assertDestinationFromConsumer(call.args ?? {}, consumerSaid());
		}
		return null;
	} catch (error) {
		return error as ToolError;
	}
}

/**
 * A refused proposal, answered with the interface that resolves it.
 *
 * The model is told why through the recorded result, but it is not asked to
 * work out the next move: what the Consumer has to supply follows from the
 * refusal, so the form or the choice is offered here and the turn ends. No
 * second guess, no retry, no third tool card saying the same thing.
 */
function askInstead(shop: string, call: ToolCall, refused: ToolError): void {
	recordToolCall(THREAD, shop, call.name, call.args, { error: refused.code, detail: refused.message });
	addMessage(
		THREAD,
		'agent',
		refused.message,
		widgetForRefusal(refused.code, call, seenCatalogue(), refused.fields)
	);
}

/** The conversation as the model should see it: messages and tool results in
 *  the order they happened, so a price it reports is one it can point at. */
function turns(): Turn[] {
	const state = thread(THREAD);
	const rows: (Turn & { at: string })[] = [
		...state.messages.map((m) => ({ role: m.role as 'consumer' | 'agent', text: m.text, at: m.at })),
		...state.toolCalls.map((c) => ({
			role: 'tool' as const,
			name: c.name,
			args: JSON.parse(c.request),
			result: c.response ? JSON.parse(c.response) : null,
			at: c.at
		}))
	];
	rows.sort((a, b) => (a.at < b.at ? -1 : 1));
	return rows.map(({ at: _at, ...turn }) => turn as Turn);
}

/**
 * Run the agent until it has something to say, it needs consent, or it hits the
 * step cap.
 *
 * The cap is not decoration: a model that keeps calling tools forever would
 * spend the Consumer's time and the shop's rate limit, and `MAX_STEPS` is where
 * that stops.
 */
async function runAgent(shops: readonly Shop[], driver: OpenRouterDriver): Promise<void> {
	for (let step = 0; step < MAX_STEPS; step += 1) {
		let turn;
		try {
			turn = await driver.step(
				turns(),
				[...reachableTools(shops)],
				schemasFor(shops),
				shopRoster(shops)
			);
		} catch (error) {
			addMessage(THREAD, 'agent', `I could not reach my model: ${(error as Error).message}`);
			return;
		}
		if (turn.kind === 'text') {
			// A message may end in one widget proposal. It is validated here and
			// stored beside the text; one that does not parse is dropped and the
			// sentence stands on its own, which is what a widget is a shortcut
			// for in the first place. Reasoning, when the model produced any,
			// rides beside the response it led to — the same interleaving
			// Claude.ai shows a thinking block in.
			const said = composeMessage(turn.text);
			if (!said.text) {
				// The model had nothing to add. It routinely does after an
				// approved write, because the app has already said what the shop
				// reported — so the turn ends here rather than filling the
				// silence. A stock "I am not sure what to do next." under a
				// correct "Basket updated!" reads as the agent losing the thread
				// it had not lost. The reasoning still lands, if there was any.
				if (turn.reasoning) addMessage(THREAD, 'agent', '', null, turn.reasoning);
				if (!hasSpokenSince()) {
					addMessage(
						THREAD,
						'agent',
						'I am not sure what to do next — tell me what you would like.'
					);
				}
				return;
			}
			addMessage(THREAD, 'agent', said.text, said.widget, turn.reasoning);
			return;
		}
		if (turn.reasoning) {
			// A turn that ends in tool calls produces no message of its own —
			// the calls run silently and render as cards. The reasoning that
			// chose them still deserves a place in the transcript, so it gets
			// one: an empty-text row that is only ever a thinking disclosure,
			// timestamped ahead of the tool cards that follow it.
			addMessage(THREAD, 'agent', '', null, turn.reasoning);
		}
		const known = reachableTools(shops);
		const planned = turn.calls.filter((call) => known.has(call.name));
		let i = 0;
		while (i < planned.length) {
			// A run of reads goes out together. Reads change nothing, so their
			// order between each other cannot matter — but it does matter against
			// a write, which is why a run stops at the first one. Two products
			// asked for together used to cost four serial round trips. None of
			// the proposal guards apply to a read: they refuse an unchosen
			// variant, an orphan add-on and an invented address, and a read
			// proposes none of those.
			const reads: ToolCall[] = [];
			while (i < planned.length && READS.has(planned[i]!.name)) {
				reads.push(planned[i]!);
				i += 1;
			}
			if (reads.length) {
				await runReads(reads, seenCatalogue(), shops);
				continue;
			}

			const call = planned[i]!;
			i += 1;
			const domains = resolve(call, seenCatalogue(), shops);
			if (domains instanceof ToolError) {
				// More than one shop could be meant, or none named and more than
				// one is known — a question for the Consumer, answered the same
				// way an invented-address refusal is: a widget, or the plain
				// sentence when there is nothing to tap.
				askInstead('', call, domains);
				return;
			}
			const domain = domains[0]!;
			const refused = proposalRefusal(domain, call, shops);
			if (refused) {
				// The refusal is recorded so the model sees why, and the interface
				// that resolves it is offered in the same breath. The turn ends
				// here: a model asked to recover from this invented a second
				// address rather than asking for the first.
				askInstead(domain, call, refused);
				return;
			}
			// Standing never covers a spend step (ALWAYS_ALLOWABLE/isMoneyPath
			// both refuse that outright), but it does cover everything else once
			// the Consumer has granted it once — live-caught: this branch used
			// to set `awaiting` unconditionally for every write, so "Always
			// allow" never actually took effect here even though it did in the
			// scripted-driver path and in requiresFreshConsent's own tests.
			if (requiresFreshConsent(call.name, standing, shops)) {
				awaiting = { call, domain };
				addMessage(THREAD, 'agent', `I’d like to call ${call.name} next — details below, your call.`);
				return;
			}
			try {
				await runCall(domain, shops, call);
			} catch (error) {
				const refusal = error as ShopError;
				recordToolCall(THREAD, domain, call.name, call.args, {
					error: refusal.code,
					detail: refusal.message
				});
				addMessage(THREAD, 'agent', refusalText(refusal));
				return;
			}
		}
	}
	addMessage(THREAD, 'agent', 'I have taken this as far as I can in one go — tell me what to do next.');
}

export const actions = {
	send: async ({ request }) => {
		const form = await request.formData();
		const text = String(form.get('text') ?? '').trim();
		if (!text) return fail(400, { message: 'Say something first.' });

		addMessage(THREAD, 'consumer', text);

		if (awaiting) {
			// A pending call is a question already asked. Planning a next step on
			// top of it would silently skip the one the Consumer is looking at.
			addMessage(
				THREAD,
				'agent',
				`Take your time — I’m still waiting on your answer about ${awaiting.call.name}. Allow or decline it and I’ll carry on.`
			);
			return { ok: true };
		}

		const shops = knownShops();
		if (!shops.length) {
			addMessage(
				THREAD,
				'agent',
				'I have no shop to talk to yet. Add one on the Shops page and I will fetch its card and keys first.'
			);
			return { ok: true };
		}

		const driver = currentDriver();
		if (driver instanceof OpenRouterDriver) {
			await runAgent(shops, driver);
			return { ok: true };
		}

		const state = thread(THREAD);
		let proposed: Proposal[];
		try {
			proposed = await driver.plan(
				state.messages.map((m) => ({ role: m.role as 'consumer' | 'agent', text: m.text })),
				[...reachableTools(shops)]
			);
		} catch (error) {
			addMessage(THREAD, 'agent', `I could not plan a next step: ${(error as Error).message}`);
			return { ok: true };
		}

		for (const proposal of proposed.slice(0, MAX_STEPS)) {
			if (isOffer(proposal)) {
				// Something only the Consumer has: the driver asks for it and stops
				// there. The answer arrives as a widget submission, in their words.
				addMessage(THREAD, 'agent', proposal.say, proposal.widget);
				return { ok: true };
			}
			const call = proposal;
			if (!reachableTools(shops).has(call.name)) {
				// A name no known shop has is refused here rather than sent.
				addMessage(THREAD, 'agent', `${call.name} is not a tool any shop you know offers.`);
				continue;
			}
			const domains = resolve(call, seenCatalogue(), shops);
			if (domains instanceof ToolError) {
				askInstead('', call, domains);
				break;
			}
			const domain = domains[0]!;
			const refused = proposalRefusal(domain, call, shops);
			if (refused) {
				askInstead(domain, call, refused);
				break;
			}
			if (requiresFreshConsent(call.name, standing, shops)) {
				awaiting = { call, domain };
				addMessage(THREAD, 'agent', `I’d like to call ${call.name} next — details below, your call.`);
				return { ok: true };
			}
			try {
				await runAndSay(domain, shops, call);
			} catch (error) {
				const refusal = error as ShopError;
				recordToolCall(THREAD, domain, call.name, call.args, {
					error: refusal.code,
					detail: refusal.message
				});
				addMessage(THREAD, 'agent', refusalText(refusal));
				break;
			}
		}
		return { ok: true };
	},

	/**
	 * A widget the Consumer filled in or tapped.
	 *
	 * **The submission is the consent.** The form shows exactly the fields that
	 * will be sent and the shopper types the values themselves, so a second
	 * modal restating them would be a prompt to click through. Spend tools are
	 * not reachable this way at all — `widgets.ts` refuses to build one — so
	 * this can never be the step that opens a checkout.
	 *
	 * The widget definition is read from the database, never from the post: the
	 * browser sends values, and the shape they go into is the one this chat
	 * stored when it offered the widget.
	 */
	widget: async ({ request }) => {
		const form = await request.formData();
		const stored = widgetFor(THREAD, Number(form.get('message')));
		if (!stored) return fail(400, { message: 'That is no longer on offer.' });

		const values: Record<string, string> = {};
		for (const [key, value] of form.entries()) {
			if (key !== 'message') values[key] = String(value);
		}

		let call: ToolCall;
		let said: string;
		try {
			const widget = parseWidget(JSON.parse(stored));
			const built = argsFor(widget, values);
			call = { name: built.tool, args: built.args };
			said = saidBySubmitting(widget, values);
		} catch (error) {
			return fail(400, { message: (error as ToolError).message });
		}

		// Recorded as the Consumer's own words first: it is how an address typed
		// into a form counts as given, and how the transcript shows that it was.
		if (said) addMessage(THREAD, 'consumer', said);

		const shops = knownShops();
		if (!shops.length) return { ok: true };
		const domains = resolve(call, seenCatalogue(), shops);
		if (domains instanceof ToolError) {
			// Tapping a variant or filling a form named no shop, and more than
			// one could answer to it — offered again as the shop-picker widget,
			// same as any other refusal a widget can resolve.
			askInstead('', call, domains);
			return { ok: true };
		}
		const domain = domains[0]!;
		try {
			await runAndSay(domain, shops, call);
		} catch (error) {
			const refusal = error as ShopError;
			recordToolCall(THREAD, domain, call.name, call.args, { error: refusal.code, detail: refusal.message });
			addMessage(THREAD, 'agent', refusalText(refusal));
		}
		const driver = currentDriver();
		if (driver instanceof OpenRouterDriver) await runAgent(shops, driver);
		return { ok: true };
	},

	/**
	 * The basket panel's own buttons: remove one line, or start over.
	 *
	 * Both are the Consumer acting on their own basket directly, which is the
	 * point — the line they cannot account for is one click from gone, without
	 * having to talk the agent into removing it.
	 */
	basket: async ({ request }) => {
		const form = await request.formData();
		const sku = String(form.get('sku') ?? '');
		const shop = String(form.get('shop') ?? '');
		const shops = knownShops();
		if (!shops.length) return { ok: true };

		if (!sku) {
			// "Start over" has no id to resolve a single shop by — and the point
			// of a fresh basket is that it is fresh everywhere, not just at
			// whichever shop happened to be dispatched to last.
			addMessage(THREAD, 'consumer', 'Start over with an empty basket.');
			await startFresh(shops);
			addMessage(THREAD, 'agent', 'Cleared every basket. Starting fresh.');
			return { ok: true };
		}

		// The button already knows which shop's basket this line is in — named
		// explicitly rather than re-derived, so removing a line never depends on
		// that SKU still being resolvable through `seen.sources`.
		const call: ToolCall = { name: 'remove-line', args: shop ? { sku, shop } : { sku } };
		const domains = resolve(call, seenCatalogue(), shops);
		if (domains instanceof ToolError) {
			askInstead('', call, domains);
			return { ok: true };
		}
		const domain = domains[0]!;
		addMessage(THREAD, 'consumer', `Remove ${sku} from the basket.`);
		try {
			await runAndSay(domain, shops, call);
		} catch (error) {
			const refusal = error as ShopError;
			recordToolCall(THREAD, domain, call.name, call.args, { error: refusal.code, detail: refusal.message });
			addMessage(THREAD, 'agent', refusalText(refusal));
		}
		return { ok: true };
	},

	decide: async ({ request }) => {
		const form = await request.formData();
		const verdict = String(form.get('verdict') ?? '');
		const pending = awaiting;
		awaiting = null;
		if (!pending) return { ok: true };
		const { call, domain } = pending;
		const shops = knownShops();

		if (verdict === 'declined') {
			addMessage(THREAD, 'agent', `Understood — I will not call ${call.name}.`);
			return { ok: true };
		}
		if (verdict === 'always') {
			// A spend step can never acquire standing approval. The closed set
			// keeps ALWAYS_ALLOWABLE's own hand-reasoned boundary; a generic
			// tool uses isMoneyPath's — see requiresFreshConsent's own comment
			// for why the two are asked separately rather than merged.
			const allowable = (TOOLS as readonly string[]).includes(call.name)
				? ALWAYS_ALLOWABLE.has(call.name)
				: !isMoneyPath(call.name, shops);
			if (allowable) standing.add(call.name);
		}

		try {
			await runAndSay(domain, shops, call);
		} catch (error) {
			const refusal = error as ShopError;
			recordToolCall(THREAD, domain, call.name, call.args, { error: refusal.code, detail: refusal.message });
			addMessage(THREAD, 'agent', refusalText(refusal));
		}
		// A real model keeps going on its own once the call it asked for has run.
		const driver = currentDriver();
		if (driver instanceof OpenRouterDriver) await runAgent(knownShops(), driver);
		return { ok: true };
	},

	reset: async () => {
		// A new thread means a new basket: the sidecar keeps one basket per
		// agent, and without this the next conversation inherits the last
		// one's lines — every one of them breaking the rule that a cart line
		// traces to something this conversation asked for or accepted. Local
		// state resets regardless; a shop that cannot be reached does not get
		// to keep the thread dirty.
		await startFresh(knownShops());
		db.prepare(`DELETE FROM messages WHERE thread_id = ?`).run(THREAD);
		db.prepare(`DELETE FROM tool_calls WHERE thread_id = ?`).run(THREAD);
		standing.clear();
		awaiting = null;
		// The scripted driver walks a pinned sequence, so a reset has to put it
		// back to the start or the next conversation resumes mid-basket.
		const driver = currentDriver() as { reset?: () => void };
		driver.reset?.();
		return { ok: true };
	},

	/**
	 * The model switcher. Only ever sets which *configured* driver answers —
	 * `driverFor` already refuses to honour a choice this process has no key
	 * or model for, so a stale or tampered value here just falls back to the
	 * operator's own deploy-time default rather than failing the request.
	 */
	driver: async ({ request }) => {
		const form = await request.formData();
		const choice = String(form.get('choice') ?? '').trim();
		if (!choice || choice === 'default') {
			clearSetting(DRIVER_SETTING);
			return { ok: true };
		}
		setSetting(DRIVER_SETTING, choice);
		return { ok: true };
	}
};
