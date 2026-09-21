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
import { SYSTEM_PROMPT, TOOL_SCHEMAS } from '../tools/loop.ts';
import type { Widget } from '../widgets.ts';

export type ToolCall = { name: string; args: Record<string, unknown> };

/** Something to say plus an interface to say it with: the driver asking the
 *  Consumer for what only the Consumer has. A driver that guessed instead
 *  would be inventing an address, which is the one thing none of them may do. */
export type Offer = { say: string; widget: Widget };

/** What a driver may propose: a call to make, or a thing to ask. */
export type Proposal = ToolCall | Offer;

export function isOffer(proposal: Proposal): proposal is Offer {
	return 'say' in proposal;
}

export type Message = { role: 'consumer' | 'agent'; text: string };

export interface ModelDriver {
	readonly name: string;
	plan(messages: Message[], tools: string[]): Promise<Proposal[]>;
}

/** §16.9's pinned sequence, exactly. */
export const SCRIPTED_SEQUENCE: Proposal[] = [
	{ name: 'search', args: { query: 'black tote' } },
	{ name: 'read-item', args: { group: 'tote' } },
	{ name: 'add-line', args: { sku: 'SD-TOTE-BLK-M', qty: 1 } },
	{ name: 'add-line', args: { sku: 'SD-GIFTWRAP', qty: 1, parent: 'SD-TOTE-BLK-M' } },
	{ name: 'add-line', args: { sku: 'SD-CHARMBAR-SEAT', qty: 1 } },
	// The address and the Contact Point are **asked for, not pinned.** They used
	// to be literals here — a Mumbai address nobody in the conversation had
	// given — and a pinned sequence is exactly where an invented address comes
	// from when the demo is the thing being copied. Asking is also the shorter
	// demo: it shows the widget the real model path uses.
	{
		say: 'Where should this go?',
		widget: {
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
		}
	},
	{
		say: 'And where should the shop send the confirmation?',
		widget: {
			kind: 'form',
			title: 'Contact point',
			submit: 'Save',
			tool: 'set-contact',
			group: 'contact',
			fields: [
				{ name: 'email', label: 'Email', kind: 'email', required: false, placeholder: '' },
				{ name: 'phone', label: 'Phone', kind: 'tel', required: false, placeholder: '' }
			]
		}
	},
	{ name: 'choose-fulfillment', args: { id: 'rest-of-india' } },
	{ name: 'start-checkout', args: {} },
	{ name: 'place-order', args: {} },
	{ name: 'order-status', args: {} }
];

export class ScriptedDriver implements ModelDriver {
	readonly name = 'scripted';
	#step = 0;

	async plan(): Promise<Proposal[]> {
		const step = SCRIPTED_SEQUENCE[this.#step];
		if (!step) return [];
		this.#step += 1;
		return [step];
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

/** One instance per choice, not one for the process: `ScriptedDriver` walks a
 *  pinned sequence and keeps its position in the instance, so rebuilding it
 *  on every request restarts the conversation at step 0 forever — which is
 *  exactly what happened before this was cached at all. Keyed by choice
 *  (rather than a single `held`) so switching models mid-session and
 *  switching back does not lose the scripted driver's position either. */
const byChoice = new Map<string, ModelDriver>();

export const DRIVER_CHOICES = ['scripted', 'openrouter', 'anthropic', 'ollama'] as const;
export type DriverChoice = (typeof DRIVER_CHOICES)[number];

export type DriverOption = { id: DriverChoice; label: string; configured: boolean };

/** What can actually be picked — only what this process has a key or a model
 *  configured for. A model switcher that offered an unconfigured driver
 *  would be an option that fails the moment it's clicked. */
export function availableDrivers(): DriverOption[] {
	return [
		{ id: 'scripted', label: 'Scripted (no model, pinned demo sequence)', configured: true },
		{
			id: 'openrouter',
			label: `OpenRouter — ${process.env.OPENROUTER_MODEL ?? 'openai/gpt-oss-20b'}`,
			configured: Boolean(process.env.OPENROUTER_API_KEY)
		},
		{
			id: 'anthropic',
			label: 'Anthropic — Claude',
			configured: Boolean(process.env.ANTHROPIC_API_KEY)
		},
		{
			id: 'ollama',
			label: `Ollama — ${process.env.OLLAMA_MODEL ?? 'no model set'}`,
			configured: Boolean(process.env.OLLAMA_MODEL)
		}
	];
}

/** `override`, when it names a configured driver, wins over
 *  `CHAT_MODEL_DRIVER` — the Consumer's own switch in the chat UI over the
 *  operator's deploy-time default. Anything else (unset, or naming a driver
 *  this process has no key for) falls back the same way `buildDriver`
 *  always has: to the env var, then to `scripted`. */
export function driverFor(override: string | null): ModelDriver {
	const options = availableDrivers();
	const wanted = override ? options.find((o) => o.id === override && o.configured) : undefined;
	const choice = wanted?.id ?? process.env.CHAT_MODEL_DRIVER ?? 'scripted';
	let driver = byChoice.get(choice);
	if (!driver) {
		driver = buildDriver(choice);
		byChoice.set(choice, driver);
	}
	return driver;
}

/** The process default: `driverFor(null)`. Kept for callers — tests, mostly
 *  — that have no Consumer override to read. */
export function driverFromEnv(): ModelDriver {
	return driverFor(null);
}

function buildDriver(choice: string): ModelDriver {
	if (choice === 'openrouter' && process.env.OPENROUTER_API_KEY) {
		return new OpenRouterDriver(
			process.env.OPENROUTER_API_KEY,
			process.env.OPENROUTER_MODEL ?? 'openai/gpt-oss-20b',
			SYSTEM_PROMPT,
			TOOL_SCHEMAS
		);
	}
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

/**
 * OpenRouter — a real model, calling tools natively.
 *
 * One API over many providers, so the demo is not tied to one vendor's key. The
 * model proposes; **deterministic code still validates and executes**, and the
 * sidecar refuses anything the Merchant would not allow. A model is a proposer
 * here and never an authority, which is why a wrong model costs a refusal
 * rather than a wrong charge.
 *
 * The conversation it sees includes every tool result verbatim, so it can read
 * a price rather than remember one — and it is told, in its system prompt, that
 * it may never state a number no tool returned.
 */
export type ToolTurn =
	| { kind: 'calls'; calls: ToolCall[]; reasoning: string | null }
	| { kind: 'text'; text: string; reasoning: string | null };

/** One entry in what the model is shown: a message, or a tool call with its
 *  result. Kept in order so the model sees the conversation as it happened. */
export type Turn =
	| { role: 'consumer' | 'agent'; text: string }
	| { role: 'tool'; name: string; args: unknown; result: unknown };

export class OpenRouterDriver implements ModelDriver {
	readonly name: string;
	constructor(
		private readonly apiKey: string,
		private readonly model: string,
		private readonly systemPrompt: string,
		private readonly toolSchemas: Record<string, { description: string; parameters: object }>
	) {
		this.name = `openrouter:${model}`;
	}

	/** The `ModelDriver` shape, for callers that only want the next calls. */
	async plan(messages: Message[], tools: string[]): Promise<ToolCall[]> {
		const turn = await this.step(messages as Turn[], tools);
		return turn.kind === 'calls' ? turn.calls : [];
	}

	/** The full turn: either tool calls to run, or something to say. */
	async step(turns: Turn[], tools: string[]): Promise<ToolTurn> {
		const body = {
			model: this.model,
			messages: [{ role: 'system', content: this.systemPrompt }, ...toOpenAI(turns)],
			tools: tools.flatMap((name) => {
				const schema = this.toolSchemas[name];
				if (!schema) return [];
				return [
					{
						type: 'function',
						function: {
							// OpenAI-style tool names allow no hyphens, and every tool
							// here has one. Mapped back on the way in.
							name: name.replace(/-/g, '_'),
							description: schema.description,
							parameters: schema.parameters
						}
					}
				];
			}),
			tool_choice: 'auto',
			// OpenRouter's unified reasoning param: a model that supports it
			// (this demo's default, openai/gpt-oss-20b, does) returns its
			// reasoning as its own field; a model that does not just ignores
			// the request rather than failing it — no capability check needed
			// on this side.
			reasoning: { effort: 'medium' }
		};

		const response = await fetch('https://openrouter.ai/api/v1/chat/completions', {
			method: 'POST',
			headers: {
				'content-type': 'application/json',
				authorization: `Bearer ${this.apiKey}`,
				// OpenRouter attributes traffic with these; they are not secrets.
				'http-referer': 'https://github.com/openstore/demo',
				'x-title': 'OpenStore buyer chat'
			},
			body: JSON.stringify(body)
		});

		const text = await response.text();
		if (!response.ok) {
			throw new Error(`OpenRouter answered ${response.status}: ${text.slice(0, 200)}`);
		}
		let payload: any;
		try {
			payload = JSON.parse(text);
		} catch {
			throw new Error(`OpenRouter returned something that is not JSON: ${text.slice(0, 120)}`);
		}
		const choice = payload?.choices?.[0]?.message;
		// A model that does not support reasoning simply has no such field;
		// one that does but produced nothing this turn returns an empty
		// string, which is exactly as uninteresting as no field at all.
		const reasoning = typeof choice?.reasoning === 'string' && choice.reasoning.trim() ? choice.reasoning.trim() : null;
		const rawCalls = choice?.tool_calls ?? [];
		if (rawCalls.length) {
			const calls: ToolCall[] = [];
			for (const call of rawCalls) {
				const name = String(call?.function?.name ?? '').replace(/_/g, '-');
				if (!tools.includes(name)) continue; // a name the shop lacks is dropped, not sent
				let args: Record<string, unknown> = {};
				try {
					args = JSON.parse(call?.function?.arguments || '{}');
				} catch {
					// Malformed arguments are dropped rather than guessed at.
					continue;
				}
				calls.push({ name, args });
			}
			if (calls.length) return { kind: 'calls', calls, reasoning };
		}
		return {
			kind: 'text',
			text: String(choice?.content ?? '').trim() || 'I am not sure what to do next.',
			reasoning
		};
	}
}

function toOpenAI(turns: Turn[]): any[] {
	const out: any[] = [];
	for (const turn of turns) {
		if (turn.role === 'tool') {
			// The call and its result as an assistant/tool pair, so the model can
			// read the shop's own numbers instead of recalling them.
			const id = `call_${out.length}`;
			out.push({
				role: 'assistant',
				tool_calls: [
					{
						id,
						type: 'function',
						function: { name: turn.name.replace(/-/g, '_'), arguments: JSON.stringify(turn.args) }
					}
				]
			});
			out.push({ role: 'tool', tool_call_id: id, content: JSON.stringify(turn.result).slice(0, 6000) });
		} else {
			out.push({ role: turn.role === 'consumer' ? 'user' : 'assistant', content: turn.text });
		}
	}
	return out;
}
