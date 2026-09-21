/**
 * The generic MCP client: initialize, tools/list, tools/call against any
 * server, with no self-registration and no bearer token — this repo's own
 * shops speak ADR-0012's admission scheme; nothing says a server that isn't
 * one of them has to.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ShopError, callGeneric, initializeGeneric, listToolsGeneric } from '../src/lib/mcp/client.ts';

afterEach(() => {
	vi.unstubAllGlobals();
});

function stub(handler: (body: any) => { status: number; body: unknown }) {
	vi.stubGlobal('fetch', async (_url: string, init: RequestInit) => {
		const body = JSON.parse(String(init.body));
		const { status, body: responseBody } = handler(body);
		return new Response(JSON.stringify(responseBody), { status });
	});
}

describe('initializeGeneric', () => {
	it('names the server from its own initialize response', async () => {
		stub(() => ({
			status: 200,
			body: { jsonrpc: '2.0', id: 1, result: { serverInfo: { name: 'Toybox' } } }
		}));
		expect(await initializeGeneric('http://toybox.example/mcp')).toEqual({ name: 'Toybox' });
	});

	it('falls back to no name when the server says nothing', async () => {
		stub(() => ({ status: 200, body: { jsonrpc: '2.0', id: 1, result: {} } }));
		expect(await initializeGeneric('http://toybox.example/mcp')).toEqual({ name: null });
	});
});

describe('listToolsGeneric', () => {
	it('defaults missing or wrongly-typed fields rather than failing on them', async () => {
		stub(() => ({
			status: 200,
			body: {
				jsonrpc: '2.0',
				id: 1,
				result: { tools: [{ name: 'roll_dice' }, { name: 'odd_shape', description: 5 }] }
			}
		}));
		const tools = await listToolsGeneric('http://toybox.example/mcp');
		expect(tools).toEqual([
			{ name: 'roll_dice', description: '', inputSchema: { type: 'object' }, annotations: {} },
			{ name: 'odd_shape', description: '', inputSchema: { type: 'object' }, annotations: {} }
		]);
	});

	it('drops a tool with no name at all rather than guessing one', async () => {
		stub(() => ({
			status: 200,
			body: { jsonrpc: '2.0', id: 1, result: { tools: [{ description: 'no name here' }] } }
		}));
		expect(await listToolsGeneric('http://toybox.example/mcp')).toEqual([]);
	});
});

describe('callGeneric', () => {
	it('returns structuredContent when the server provides it', async () => {
		stub(() => ({
			status: 200,
			body: {
				jsonrpc: '2.0',
				id: 1,
				result: { content: [], structuredContent: { value: 4 }, isError: false }
			}
		}));
		expect(await callGeneric('http://toybox.example/mcp', 'roll_dice', { sides: 6 })).toEqual({
			value: 4
		});
	});

	it('parses JSON out of a text-only response when no structuredContent is given', async () => {
		stub(() => ({
			status: 200,
			body: {
				jsonrpc: '2.0',
				id: 1,
				result: { content: [{ type: 'text', text: '{"value":4}' }], isError: false }
			}
		}));
		expect(await callGeneric('http://toybox.example/mcp', 'roll_dice', {})).toEqual({ value: 4 });
	});

	it('wraps genuinely plain text rather than failing to parse it', async () => {
		stub(() => ({
			status: 200,
			body: { jsonrpc: '2.0', id: 1, result: { content: [{ type: 'text', text: 'four' }], isError: false } }
		}));
		expect(await callGeneric('http://toybox.example/mcp', 'roll_dice', {})).toEqual({ text: 'four' });
	});

	it('throws on isError with the content as the message', async () => {
		stub(() => ({
			status: 200,
			body: {
				jsonrpc: '2.0',
				id: 1,
				result: { content: [{ type: 'text', text: 'sides must be positive' }], isError: true }
			}
		}));
		await expect(callGeneric('http://toybox.example/mcp', 'roll_dice', { sides: -1 })).rejects.toThrow(
			/sides must be positive/
		);
	});

	it('refuses plainly when the server demands auth this client cannot provide', async () => {
		vi.stubGlobal('fetch', async () => new Response('{}', { status: 401 }));
		try {
			await callGeneric('http://toybox.example/mcp', 'roll_dice', {});
			expect.unreachable();
		} catch (error) {
			expect(error).toBeInstanceOf(ShopError);
			expect((error as ShopError).code).toBe('auth-required');
		}
	});

	it('gives up on a server that never answers, rather than hanging forever', async () => {
		// A server this slow to respond is indistinguishable, from here, from
		// one that will never respond at all — and a request with no timeout
		// hangs identically either way. Fake timers so the test proves the
		// timeout fires without actually waiting it out.
		vi.useFakeTimers();
		vi.stubGlobal('fetch', async (_url: string, init: RequestInit) => {
			return new Promise((_resolve, reject) => {
				init.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
			});
		});
		// Attached before advancing time, so the rejection is never briefly
		// unhandled — only the assertion happens after.
		const pending = callGeneric('http://toybox.example/mcp', 'roll_dice', {}).catch((error) => error);
		await vi.advanceTimersByTimeAsync(15_000);
		expect(await pending).toMatchObject({ code: 'unreachable' });
		vi.useRealTimers();
	});

	it('refuses a response larger than this chat will read', async () => {
		const huge = 'x'.repeat(300 * 1024);
		vi.stubGlobal(
			'fetch',
			async () =>
				new Response(
					JSON.stringify({
						jsonrpc: '2.0',
						id: 1,
						result: { content: [{ type: 'text', text: huge }], isError: false }
					}),
					{ status: 200 }
				)
		);
		await expect(callGeneric('http://toybox.example/mcp', 'roll_dice', {})).rejects.toMatchObject({
			code: 'too-large'
		});
	});
});
