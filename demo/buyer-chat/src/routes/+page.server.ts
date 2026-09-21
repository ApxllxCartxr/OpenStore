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
import { addMessage, createThread, recordToolCall, thread } from '$lib/session.ts';
import { driverFromEnv, OpenRouterDriver, type ToolCall, type Turn } from '$lib/model/driver.ts';
import { ALWAYS_ALLOWABLE, MAX_STEPS, SCOPES, TOOLS, type ToolName } from '$lib/tools/loop.ts';
import { call as callShop, ShopError } from '$lib/mcp/client.ts';
import { db } from '$lib/session.ts';

const THREAD = 'thread_demo';

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
	if (!state.messages.length) {
		addMessage(
			THREAD,
			'agent',
			'Tell me what you are looking for and I will search the shop. I can browse and ' +
				'build a basket — I cannot spend. Every purchase ends with you approving the ' +
				'exact amount on the shop’s own page.'
		);
	}
	return {
		thread: thread(THREAD),
		driver: driverFromEnv().name,
		tools: [...TOOLS],
		shop: currentShop(),
		pending: awaiting,
		standing: [...standing]
	};
}

function describe(tool: string, result: Record<string, any>): string {
	/** What to say about a result. Never a number this file invented. */
	if (tool === 'search') {
		const results = (result.results ?? []) as any[];
		if (!results.length) return 'The shop has nothing matching that.';
		return `The shop returned ${results.length} ${results.length === 1 ? 'group' : 'groups'}.`;
	}
	if (tool === 'read-item') {
		return `${result.name}: ${(result.variants ?? []).length} variants.`;
	}
	if (tool === 'start-checkout') {
		return `The shop quoted this order. Approving it is yours to do, not mine.`;
	}
	if (tool === 'place-order') {
		return 'Here is the approval link. You approve the exact amount on the shop’s own page.';
	}
	if (result.total_minor !== undefined && result.total_minor !== null) {
		return `The shop priced the basket at ${formatMinor(Number(result.total_minor))}.`;
	}
	if (result.needs) {
		return `Basket updated. Still needed: ${(result.needs as string[]).join(', ')}.`;
	}
	return 'Done.';
}

function formatMinor(minor: number): string {
	// Display only — the shop's own integer, never arithmetic on a price.
	return `₹${(minor / 100).toFixed(2)}`;
}

async function runCall(domain: string, call: ToolCall): Promise<Record<string, any>> {
	const result = await callShop(domain, call.name, call.args);
	recordToolCall(THREAD, call.name, call.args, result);
	return result;
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
			addMessage(THREAD, 'agent', turn.text);
			return;
		}
		for (const call of turn.calls) {
			if (!TOOLS.includes(call.name as ToolName)) continue;
			if (!ALWAYS_ALLOWABLE.has(call.name) && !standing.has(call.name)) {
				awaiting = call;
				addMessage(
					THREAD,
					'agent',
					`I would like to call ${call.name} (scope: ${SCOPES[call.name as ToolName]}). Your call.`
				);
				return;
			}
			try {
				await runCall(domain, call);
			} catch (error) {
				const refusal = error as ShopError;
				// Recorded as a result, not swallowed: the model has to see the
				// refusal to stop repeating the call that caused it.
				recordToolCall(THREAD, call.name, call.args, {
					error: refusal.code,
					detail: refusal.message
				});
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
				`I am still waiting on your answer about ${awaiting.name}. Allow or decline it and I will carry on.`
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
		let proposed: ToolCall[];
		try {
			proposed = await driver.plan(
				state.messages.map((m) => ({ role: m.role as 'consumer' | 'agent', text: m.text })),
				[...TOOLS]
			);
		} catch (error) {
			addMessage(THREAD, 'agent', `I could not plan a next step: ${(error as Error).message}`);
			return { ok: true };
		}

		for (const call of proposed.slice(0, MAX_STEPS)) {
			if (!TOOLS.includes(call.name as ToolName)) {
				// A name the shop does not have is refused here rather than sent.
				addMessage(THREAD, 'agent', `${call.name} is not a tool this shop offers.`);
				continue;
			}
			if (!ALWAYS_ALLOWABLE.has(call.name) && !standing.has(call.name)) {
				awaiting = call;
				addMessage(
					THREAD,
					'agent',
					`I would like to call ${call.name} (scope: ${SCOPES[call.name as ToolName]}). ` +
						`Your call.`
				);
				return { ok: true };
			}
			try {
				const result = await runCall(shop.domain, call);
				addMessage(THREAD, 'agent', describe(call.name, result));
			} catch (error) {
				const refusal = error as ShopError;
				recordToolCall(THREAD, call.name, call.args, { error: refusal.code });
				addMessage(THREAD, 'agent', `The shop refused: ${refusal.code} — ${refusal.message}`);
				break;
			}
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
			const result = await runCall(shop.domain, call);
			addMessage(THREAD, 'agent', describe(call.name, result));
		} catch (error) {
			const refusal = error as ShopError;
			recordToolCall(THREAD, call.name, call.args, { error: refusal.code, detail: refusal.message });
			addMessage(THREAD, 'agent', `The shop refused: ${refusal.code} — ${refusal.message}`);
		}
		// A real model keeps going on its own once the call it asked for has run.
		const driver = driverFromEnv();
		if (driver instanceof OpenRouterDriver) await runAgent(shop.domain, driver);
		return { ok: true };
	},

	reset: async () => {
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
