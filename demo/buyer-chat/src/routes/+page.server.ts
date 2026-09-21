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
import { THREAD, addMessage, createThread, recordToolCall, thread, widgetFor } from '$lib/session.ts';
import {
	driverFromEnv,
	isOffer,
	OpenRouterDriver,
	type Proposal,
	type ToolCall,
	type Turn
} from '$lib/model/driver.ts';
import {
	ALWAYS_ALLOWABLE,
	MAX_STEPS,
	TOOLS,
	ToolError,
	assertAddonHasParent,
	assertDestinationFromConsumer,
	assertResolvedVariant,
	assertVariantChosenByConsumer,
	catalogueFrom,
	consentNote,
	hasMeaningfulArgs,
	validate,
	type Seen,
	type ToolName
} from '$lib/tools/loop.ts';
import { call as callShop, ShopError } from '$lib/mcp/client.ts';
import { explain } from '$lib/quote.ts';
import { argsFor, composeMessage, parseWidget, saidBySubmitting, widgetForRefusal } from '$lib/widgets.ts';
import { db } from '$lib/session.ts';

/** The shop this thread is talking to: the most recently added contact.
 *  A chat with no shop can still be typed into — it just says so. */
function currentShop(): { domain: string; name: string } | null {
	const row = db
		.prepare(`SELECT domain, name FROM contacts ORDER BY added_at DESC LIMIT 1`)
		.get() as { domain: string; name: string } | undefined;
	return row ?? null;
}

/** Standing permission is per thread and per tool, and it never covers a spend
 *  step — `ALWAYS_ALLOWABLE` is the whole of what it can hold (ADR-0008). */
const standing = new Set<string>();
/** A proposed call waiting on the Consumer. One at a time, deliberately: a
 *  queue of consent prompts is how people click through them. */
let awaiting: ToolCall | null = null;

export async function load() {
	createThread(THREAD, 'SpoiledDuckie');
	const state = thread(THREAD);
	if (!state.messages.length && !state.toolCalls.length) {
		await startFresh();
		addMessage(
			THREAD,
			'agent',
			'Hey, I’m Miro! Tell me what you’re looking for and I’ll search the shop. ' +
				'I can browse and build a basket — I can’t spend. Every purchase ends with you ' +
				'approving the exact amount on the shop’s own page.'
		);
	}
	return {
		thread: thread(THREAD),
		driver: driverFromEnv().name,
		tools: [...TOOLS],
		shop: currentShop(),
		pending: awaiting,
		pendingNote: awaiting ? consentNote(awaiting) : null,
		pendingHasArgs: awaiting ? hasMeaningfulArgs(awaiting.args) : false,
		standing: [...standing]
	};
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
async function startFresh(): Promise<void> {
	const shop = currentShop();
	if (!shop) return;
	try {
		const result = await callShop(shop.domain, 'clear-basket', {});
		// Recorded only when it did something: an empty clear on a fresh thread
		// is noise, but removed lines are exactly what the shopper must see.
		if (((result.removed ?? []) as string[]).length) {
			recordToolCall(THREAD, shop.domain, 'clear-basket', {}, result);
		}
	} catch (error) {
		const refusal = error as ShopError;
		recordToolCall(THREAD, shop.domain, 'clear-basket', {}, { error: refusal.code, detail: refusal.message });
	}
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

async function runCall(domain: string, call: ToolCall): Promise<Record<string, any>> {
	const result = await callShop(domain, call.name, call.args);
	recordToolCall(THREAD, domain, call.name, call.args, result);
	return result;
}

/** Reads, concurrently. Recorded in the order the model planned them rather
 *  than the order they came back, so the transcript reads as one decision. */
async function runReads(domain: string, reads: readonly ToolCall[]): Promise<void> {
	const settled = await Promise.allSettled(
		reads.map((read) => callShop(domain, read.name, read.args))
	);
	settled.forEach((outcome, n) => {
		const read = reads[n]!;
		if (outcome.status === 'fulfilled') {
			recordToolCall(THREAD, domain, read.name, read.args, outcome.value);
			return;
		}
		const refusal = outcome.reason as ShopError;
		recordToolCall(THREAD, domain, read.name, read.args, { error: refusal.code, detail: refusal.message });
	});
}

/** Run a call and say what the shop said back. One place, because the `needs`
 *  comparison has to be taken *before* the new result is recorded. */
async function runAndSay(domain: string, call: ToolCall): Promise<void> {
	const before = previousNeedsOf(recordedCalls());
	const result = await runCall(domain, call);
	addMessage(THREAD, 'agent', describe(call.name, result, before));
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
function proposalRefusal(shop: string, call: ToolCall): ToolError | null {
	try {
		// Shape first: a malformed call is refused here rather than spent as a
		// round trip to a shop that would refuse the same thing.
		validate(call);
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
	addMessage(THREAD, 'agent', refused.message, widgetForRefusal(refused.code, call, seenCatalogue()));
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
async function runAgent(domain: string, driver: OpenRouterDriver): Promise<void> {
	for (let step = 0; step < MAX_STEPS; step += 1) {
		let turn;
		try {
			turn = await driver.step(turns(), [...TOOLS]);
		} catch (error) {
			addMessage(THREAD, 'agent', `I could not reach my model: ${(error as Error).message}`);
			return;
		}
		if (turn.kind === 'text') {
			// A message may end in one widget proposal. It is validated here and
			// stored beside the text; one that does not parse is dropped and the
			// sentence stands on its own, which is what a widget is a shortcut
			// for in the first place.
			const said = composeMessage(turn.text);
			addMessage(THREAD, 'agent', said.text, said.widget);
			return;
		}
		const planned = turn.calls.filter((call) => TOOLS.includes(call.name as ToolName));
		let i = 0;
		while (i < planned.length) {
			// A run of reads goes to the shop at once. Reads change nothing, so
			// their order between each other cannot matter — but it does matter
			// against a write, which is why a run stops at the first one. Two
			// products asked for together used to cost four serial round trips.
			// None of the proposal guards apply to a read: they refuse an
			// unchosen variant, an orphan add-on and an invented address, and a
			// read proposes none of those.
			const reads: ToolCall[] = [];
			while (i < planned.length && ALWAYS_ALLOWABLE.has(planned[i]!.name)) {
				reads.push(planned[i]!);
				i += 1;
			}
			if (reads.length) {
				await runReads(domain, reads);
				continue;
			}

			const call = planned[i]!;
			i += 1;
			const refused = proposalRefusal(domain, call);
			if (refused) {
				// The refusal is recorded so the model sees why, and the interface
				// that resolves it is offered in the same breath. The turn ends
				// here: a model asked to recover from this invented a second
				// address rather than asking for the first.
				askInstead(domain, call, refused);
				return;
			}
			// Everything past the read batch above needs a fresh answer: standing
			// approval only ever holds reads (ADR-0008), so anything reaching
			// here is a call the Consumer has not yet allowed.
			awaiting = call;
			addMessage(THREAD, 'agent', `I’d like to call ${call.name} next — details below, your call.`);
			return;
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
				`Take your time — I’m still waiting on your answer about ${awaiting.name}. Allow or decline it and I’ll carry on.`
			);
			return { ok: true };
		}

		const shop = currentShop();
		if (!shop) {
			addMessage(
				THREAD,
				'agent',
				'I have no shop to talk to yet. Add one on the Shops page and I will fetch its card and keys first.'
			);
			return { ok: true };
		}

		const driver = driverFromEnv();
		if (driver instanceof OpenRouterDriver) {
			await runAgent(shop.domain, driver);
			return { ok: true };
		}

		const state = thread(THREAD);
		let proposed: Proposal[];
		try {
			proposed = await driver.plan(
				state.messages.map((m) => ({ role: m.role as 'consumer' | 'agent', text: m.text })),
				[...TOOLS]
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
			if (!TOOLS.includes(call.name as ToolName)) {
				// A name the shop does not have is refused here rather than sent.
				addMessage(THREAD, 'agent', `${call.name} is not a tool this shop offers.`);
				continue;
			}
			const refused = proposalRefusal(shop.domain, call);
			if (refused) {
				askInstead(shop.domain, call, refused);
				break;
			}
			if (!ALWAYS_ALLOWABLE.has(call.name) && !standing.has(call.name)) {
				awaiting = call;
				addMessage(THREAD, 'agent', `I’d like to call ${call.name} next — details below, your call.`);
				return { ok: true };
			}
			try {
				await runAndSay(shop.domain, call);
			} catch (error) {
				const refusal = error as ShopError;
				recordToolCall(THREAD, shop.domain, call.name, call.args, {
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

		const shop = currentShop();
		if (!shop) return { ok: true };
		try {
			await runAndSay(shop.domain, call);
		} catch (error) {
			const refusal = error as ShopError;
			recordToolCall(THREAD, shop.domain, call.name, call.args, { error: refusal.code, detail: refusal.message });
			addMessage(THREAD, 'agent', refusalText(refusal));
		}
		const driver = driverFromEnv();
		if (driver instanceof OpenRouterDriver) await runAgent(shop.domain, driver);
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
		const call: ToolCall = sku
			? { name: 'remove-line', args: { sku } }
			: { name: 'clear-basket', args: {} };
		const shop = currentShop();
		if (!shop) return { ok: true };
		addMessage(THREAD, 'consumer', sku ? `Remove ${sku} from the basket.` : 'Start over with an empty basket.');
		try {
			await runAndSay(shop.domain, call);
		} catch (error) {
			const refusal = error as ShopError;
			recordToolCall(THREAD, shop.domain, call.name, call.args, { error: refusal.code, detail: refusal.message });
			addMessage(THREAD, 'agent', refusalText(refusal));
		}
		return { ok: true };
	},

	decide: async ({ request }) => {
		const form = await request.formData();
		const verdict = String(form.get('verdict') ?? '');
		const call = awaiting;
		awaiting = null;
		if (!call) return { ok: true };

		if (verdict === 'declined') {
			addMessage(THREAD, 'agent', `Understood — I will not call ${call.name}.`);
			return { ok: true };
		}
		if (verdict === 'always') {
			// Reads only. A spend step can never acquire standing approval.
			if (ALWAYS_ALLOWABLE.has(call.name)) standing.add(call.name);
		}

		const shop = currentShop();
		if (!shop) return { ok: true };
		try {
			await runAndSay(shop.domain, call);
		} catch (error) {
			const refusal = error as ShopError;
			recordToolCall(THREAD, shop.domain, call.name, call.args, { error: refusal.code, detail: refusal.message });
			addMessage(THREAD, 'agent', refusalText(refusal));
		}
		// A real model keeps going on its own once the call it asked for has run.
		const driver = driverFromEnv();
		if (driver instanceof OpenRouterDriver) await runAgent(shop.domain, driver);
		return { ok: true };
	},

	reset: async () => {
		// A new thread means a new basket: the sidecar keeps one basket per
		// agent, and without this the next conversation inherits the last
		// one's lines — every one of them breaking the rule that a cart line
		// traces to something this conversation asked for or accepted. Local
		// state resets regardless; a shop that cannot be reached does not get
		// to keep the thread dirty.
		const shop = currentShop();
		if (shop) {
			try {
				await callShop(shop.domain, 'clear-basket', {});
			} catch {
				// Best effort: the transcript below still starts fresh.
			}
		}
		db.prepare(`DELETE FROM messages WHERE thread_id = ?`).run(THREAD);
		db.prepare(`DELETE FROM tool_calls WHERE thread_id = ?`).run(THREAD);
		standing.clear();
		awaiting = null;
		// The scripted driver walks a pinned sequence, so a reset has to put it
		// back to the start or the next conversation resumes mid-basket.
		const driver = driverFromEnv() as { reset?: () => void };
		driver.reset?.();
		return { ok: true };
	}
};
