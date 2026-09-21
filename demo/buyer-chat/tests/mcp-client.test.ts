/**
 * Admission, from the chat's side: how often it registers.
 *
 * Registration is capped at five an hour per IP (§16.8), so "how often" is not
 * housekeeping — a chat that re-registers on every restart runs out and the
 * shopper is told the shop is rate-limiting them.
 */
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

let directory: string;
let registrations: number;
let calls: number;
/** Status for the next `/agent/mcp` answer, so a stale token can be simulated. */
let mcpStatus: number;

function stubShop(): void {
	registrations = 0;
	calls = 0;
	mcpStatus = 200;
	vi.stubGlobal('fetch', async (url: string) => {
		if (String(url).endsWith('/agent/register')) {
			registrations += 1;
			return new Response(
				JSON.stringify({
					agent_id: 'agent_chat',
					access_token: `tok_${registrations}`,
					expires_at: new Date(Date.now() + 3_600_000).toISOString()
				}),
				{ status: 200 }
			);
		}
		calls += 1;
		const status = mcpStatus;
		// One stale answer, then the shop accepts the fresh token.
		mcpStatus = 200;
		if (status === 401) {
			return new Response(JSON.stringify({ error: { code: 'signature-invalid', detail: 'no' } }), {
				status: 401
			});
		}
		return new Response(
			JSON.stringify({
				jsonrpc: '2.0',
				id: 1,
				result: { content: [], structuredContent: { lines: [] }, isError: false }
			}),
			{ status: 200 }
		);
	});
}

/** A fresh module instance, which is what a restarted container is: the token
 *  file on the volume survives, the in-memory map does not. */
async function restart() {
	vi.resetModules();
	return import('../src/lib/mcp/client.ts');
}

beforeEach(() => {
	directory = mkdtempSync(join(tmpdir(), 'chat-tokens-'));
	process.env.CHAT_TOKEN_PATH = join(directory, 'agent-tokens.json');
	process.env.OPENSTORE_DEV_PROFILE_HOSTS = 'shop.test';
	stubShop();
});

afterEach(() => {
	vi.unstubAllGlobals();
	rmSync(directory, { recursive: true, force: true });
	delete process.env.CHAT_TOKEN_PATH;
});

describe('the token outlives the process that was issued it', () => {
	it('registers once, however many calls follow', async () => {
		const client = await restart();
		await client.call('shop.test', 'search', { query: '' });
		await client.call('shop.test', 'search', { query: 'tote' });
		expect(registrations).toBe(1);
		expect(calls).toBe(2);
	});

	it('does not register again after a restart', async () => {
		const first = await restart();
		await first.call('shop.test', 'search', {});
		const second = await restart();
		await second.call('shop.test', 'search', {});
		expect(registrations).toBe(1);
	});

	it('registers again when the stored token has expired', async () => {
		const client = await restart();
		await client.call('shop.test', 'search', {});
		vi.setSystemTime(new Date(Date.now() + 7_200_000));
		const later = await restart();
		await later.call('shop.test', 'search', {});
		expect(registrations).toBe(2);
		vi.useRealTimers();
	});

	it('re-registers once when the shop no longer knows the token, and answers the call', async () => {
		const client = await restart();
		await client.call('shop.test', 'search', {});
		mcpStatus = 401;
		const result = await client.call('shop.test', 'search', {});
		expect(result).toEqual({ lines: [] });
		expect(registrations).toBe(2);
	});

	it('gives up rather than registering twice for one call', async () => {
		// A shop refusing the fresh token is refusing this agent; retrying that
		// is how the hourly registration budget is spent on nothing.
		vi.stubGlobal('fetch', async (url: string) => {
			if (String(url).endsWith('/agent/register')) {
				registrations += 1;
				return new Response(
					JSON.stringify({
						agent_id: 'agent_chat',
						access_token: 'tok',
						expires_at: new Date(Date.now() + 3_600_000).toISOString()
					}),
					{ status: 200 }
				);
			}
			return new Response(
				JSON.stringify({ error: { code: 'signature-invalid', detail: 'This surface needs a token.' } }),
				{ status: 401 }
			);
		});
		const client = await restart();
		await expect(client.call('shop.test', 'search', {})).rejects.toThrow(/needs a token/);
		expect(registrations).toBe(2);
	});
});
