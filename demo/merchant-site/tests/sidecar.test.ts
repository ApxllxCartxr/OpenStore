/**
 * The merchant site's own outbound call to the sidecar — refund, shop-reject,
 * COD collection and RTO all go through it. Nothing here ever timed out
 * before; a hung sidecar hung the operator's action behind it forever.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { callSidecar, SidecarError } from '../src/lib/sidecar.ts';

afterEach(() => {
	vi.unstubAllGlobals();
	vi.useRealTimers();
});

describe('callSidecar', () => {
	it('gives up on a sidecar that never answers, rather than hanging forever', async () => {
		vi.useFakeTimers();
		vi.stubGlobal('fetch', async (_url: string, init: RequestInit) => {
			return new Promise((_resolve, reject) => {
				init.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
			});
		});
		const pending = callSidecar('/agentic/refund', {}, 'idem-1').catch((error) => error);
		await vi.advanceTimersByTimeAsync(15_000);
		const error = await pending;
		expect(error).toBeInstanceOf(SidecarError);
		expect((error as SidecarError).code).toBe('protocol');
	});

	it('still answers normally when the sidecar responds in time', async () => {
		vi.stubGlobal(
			'fetch',
			async () => new Response(JSON.stringify({ ok: true }), { status: 200 })
		);
		await expect(callSidecar('/agentic/refund', {}, 'idem-2')).resolves.toEqual({ ok: true });
	});
});
