/**
 * The closed set's tool names are hyphenated; OpenAI-style function names
 * are sent with underscores instead and mapped back on the way in. A blind
 * reversal (every underscore becomes a hyphen) is correct for the closed
 * set and wrong for anything else — a generic MCP tool's name is whatever
 * that server chose, underscores as often as not (`roll_dice`), and
 * reversing it produced a name nothing declares (`roll-dice`). Caught live
 * against the real running model before this test existed.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { OpenRouterDriver } from '../src/lib/model/driver.ts';

afterEach(() => vi.unstubAllGlobals());

function stubOpenRouterToolCall(functionName: string, args: Record<string, unknown>) {
	vi.stubGlobal('fetch', async () =>
		new Response(
			JSON.stringify({
				choices: [
					{
						message: {
							role: 'assistant',
							content: null,
							tool_calls: [
								{ type: 'function', function: { name: functionName, arguments: JSON.stringify(args) } }
							]
						},
						finish_reason: 'tool_calls'
					}
				]
			}),
			{ status: 200 }
		)
	);
}

describe('OpenRouterDriver tool name round-trip', () => {
	it('maps a generic (underscored) tool name back to itself, not a guess', async () => {
		stubOpenRouterToolCall('roll_dice', { sides: 20 });
		const driver = new OpenRouterDriver('key', 'model', 'system', {
			roll_dice: { description: 'Roll a die.', parameters: { type: 'object' } }
		});
		const turn = await driver.step([], ['roll_dice']);
		expect(turn).toEqual({ kind: 'calls', calls: [{ name: 'roll_dice', args: { sides: 20 } }], reasoning: null });
	});

	it('still maps the closed set\'s hyphenated names correctly', async () => {
		stubOpenRouterToolCall('add_line', { sku: 'SD-TOTE-BLK-M', qty: 1 });
		const driver = new OpenRouterDriver('key', 'model', 'system', {
			'add-line': { description: 'Add a line.', parameters: { type: 'object' } }
		});
		const turn = await driver.step([], ['add-line']);
		expect(turn).toEqual({
			kind: 'calls',
			calls: [{ name: 'add-line', args: { sku: 'SD-TOTE-BLK-M', qty: 1 } }],
			reasoning: null
		});
	});

	it('drops a call to a name that was never actually offered', async () => {
		stubOpenRouterToolCall('never_offered', {});
		const driver = new OpenRouterDriver('key', 'model', 'system', {
			roll_dice: { description: 'Roll a die.', parameters: { type: 'object' } }
		});
		const turn = await driver.step([], ['roll_dice']);
		expect(turn).toEqual({ kind: 'text', text: 'I am not sure what to do next.', reasoning: null });
	});
});
