/**
 * The model seam (D7): `plan(messages, tools) -> ToolCall[]`.
 *
 * Three drivers, one interface. `scripted` is the **default and what CI runs**,
 * and it is not a fake — it is the same code path with the proposer pinned.
 * Deterministic code validates every proposed call against real Merchant data
 * before anything renders or is sent, so the driver's only job is proposing.
 *
 * `ollama` and `anthropic` are used only if their model or key is present, and
 * **neither is ever required** for the demo or for CI. A demo that needs a
 * running LLM to pass its own tests is a demo that fails on somebody else's
 * laptop.
 */
export type ToolCall = { name: string; args: Record<string, unknown> };

export type Message = { role: 'consumer' | 'agent'; text: string };

export interface ModelDriver {
	readonly name: string;
	plan(messages: Message[], tools: string[]): Promise<ToolCall[]>;
}

/** §16.9's pinned sequence, exactly. */
export const SCRIPTED_SEQUENCE: ToolCall[] = [
	{ name: 'search', args: { query: 'black tote' } },
	{ name: 'read-item', args: { group: 'tote' } },
	{ name: 'add-line', args: { sku: 'SD-TOTE-BLK-M', qty: 1 } },
	{ name: 'add-line', args: { sku: 'SD-GIFTWRAP', qty: 1, parent: 'SD-TOTE-BLK-M' } },
	{ name: 'add-line', args: { sku: 'SD-CHARMBAR-SEAT', qty: 1 } },
	{
		name: 'set-destination',
		args: {
			destination: {
				line1: 'Dadar West',
				city: 'Mumbai',
				state: 'MH',
				postal_code: '400028',
				country: 'IN'
			}
		}
	},
	{
		name: 'set-contact',
		args: { contact: { phone: '+919000000001', email: 'demo@spoiledduckie.test' } }
	},
	{ name: 'choose-fulfillment', args: { id: 'rest-of-india' } },
	{ name: 'start-checkout', args: {} },
	{ name: 'place-order', args: {} },
	{ name: 'order-status', args: {} }
];

export class ScriptedDriver implements ModelDriver {
	readonly name = 'scripted';
	#step = 0;

	async plan(): Promise<ToolCall[]> {
		const call = SCRIPTED_SEQUENCE[this.#step];
		if (!call) return [];
		this.#step += 1;
		return [call];
	}

	reset(): void {
		this.#step = 0;
	}
}

export class OllamaDriver implements ModelDriver {
	readonly name = 'ollama';
	constructor(
		private readonly baseUrl: string,
		private readonly model: string
	) {}

	async plan(messages: Message[], tools: string[]): Promise<ToolCall[]> {
		const response = await fetch(`${this.baseUrl}/api/chat`, {
			method: 'POST',
			headers: { 'content-type': 'application/json' },
			body: JSON.stringify({
				model: this.model,
				stream: false,
				messages: [
					{
						role: 'system',
						content:
							`You may call only these tools: ${tools.join(', ')}. ` +
							`Reply with JSON {"calls":[{"name","args"}]} and nothing else. ` +
							`You never invent a price, a total, or a stock count.`
					},
					...messages.map((m) => ({
						role: m.role === 'consumer' ? 'user' : 'assistant',
						content: m.text
					}))
				]
			})
		});
		return parseCalls(await response.text(), tools);
	}
}

export class AnthropicDriver implements ModelDriver {
	readonly name = 'anthropic';
	constructor(
		private readonly apiKey: string,
		private readonly model = 'claude-sonnet-5'
	) {}

	async plan(messages: Message[], tools: string[]): Promise<ToolCall[]> {
		const response = await fetch('https://api.anthropic.com/v1/messages', {
			method: 'POST',
			headers: {
				'content-type': 'application/json',
				'x-api-key': this.apiKey,
				'anthropic-version': '2023-06-01'
			},
			body: JSON.stringify({
				model: this.model,
				max_tokens: 1024,
				system:
					`You may call only these tools: ${tools.join(', ')}. ` +
					`Reply with JSON {"calls":[{"name","args"}]} and nothing else.`,
				messages: messages.map((m) => ({
					role: m.role === 'consumer' ? 'user' : 'assistant',
					content: m.text
				}))
			})
		});
		return parseCalls(await response.text(), tools);
	}
}

export class MalformedPlan extends Error {}

/**
 * Parse whatever the model said, and refuse anything outside the closed set.
 *
 * **No state changes on a malformed plan.** A model that proposes a tool that
 * does not exist, or arguments that are not an object, gets a named error and
 * the thread is unchanged — never a partial application of the calls that did
 * parse.
 */
export function parseCalls(raw: string, tools: string[]): ToolCall[] {
	let document: unknown;
	try {
		// Models wrap JSON in prose more often than not.
		const match = /\{[\s\S]*\}/.exec(raw);
		document = JSON.parse(match ? match[0] : raw);
	} catch {
		throw new MalformedPlan('the model did not return JSON');
	}

	const payload = document as { calls?: unknown; message?: { content?: { text?: string }[] } };
	// Anthropic wraps the reply in content blocks.
	if (!payload.calls && payload.message?.content?.[0]?.text) {
		return parseCalls(payload.message.content[0].text, tools);
	}

	if (!Array.isArray(payload.calls)) throw new MalformedPlan('no calls array');

	const allowed = new Set(tools);
	return payload.calls.map((entry) => {
		const call = entry as { name?: unknown; args?: unknown };
		if (typeof call.name !== 'string' || !allowed.has(call.name)) {
			throw new MalformedPlan(`${String(call.name)} is not a tool this agent has`);
		}
		if (call.args !== undefined && (typeof call.args !== 'object' || call.args === null)) {
			throw new MalformedPlan(`${call.name}: args must be an object`);
		}
		return { name: call.name, args: (call.args ?? {}) as Record<string, unknown> };
	});
}

export function driverFromEnv(): ModelDriver {
	const choice = process.env.CHAT_MODEL_DRIVER ?? 'scripted';
	if (choice === 'ollama' && process.env.OLLAMA_MODEL) {
		return new OllamaDriver(
			process.env.OLLAMA_BASE_URL ?? 'http://localhost:11434',
			process.env.OLLAMA_MODEL
		);
	}
	if (choice === 'anthropic' && process.env.ANTHROPIC_API_KEY) {
		return new AnthropicDriver(process.env.ANTHROPIC_API_KEY);
	}
	// Falls back rather than failing: the demo must run with no model at all.
	return new ScriptedDriver();
}
