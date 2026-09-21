/**
 * The export is the page's own claim in a file: everything that happened, in
 * the order it happened, with the tool JSON verbatim.
 */
import { describe, expect, it } from 'vitest';
import {
	transcriptJson,
	transcriptMarkdown,
	type TranscriptThread
} from '../src/lib/transcript.ts';

const META = { shop: { domain: 'shop.test', name: 'SpoiledDuckie' }, driver: 'form', exportedAt: '2026-09-21T10:00:00.000Z' };

const THREAD: TranscriptThread = {
	messages: [
		{ id: 1, role: 'consumer', text: 'a tote', widget: null, at: '2026-09-21T09:00:00.000Z' },
		{
			id: 2,
			role: 'agent',
			text: 'Which one?',
			widget: '{"kind":"chips","options":["canvas","jute"]}',
			at: '2026-09-21T09:00:02.000Z'
		}
	],
	toolCalls: [
		{
			name: 'search',
			request: '{"q":"tote"}',
			// Stored as the string "null" when a call returned nothing.
			response: '{"results":[{"name":"Canvas tote","from_minor":49900}]}',
			at: '2026-09-21T09:00:01.000Z'
		}
	]
};

describe('transcript export', () => {
	it('interleaves messages and tool calls by time, not by kind', () => {
		const md = transcriptMarkdown(THREAD, META);
		const [said, tool, asked] = ['a tote', 'tool: search', 'Which one?'].map((s) =>
			md.indexOf(s)
		) as [number, number, number];
		expect(said).toBeGreaterThan(-1);
		expect(said).toBeLessThan(tool);
		expect(tool).toBeLessThan(asked);
	});

	it('carries the exact request and response JSON, not a summary', () => {
		const md = transcriptMarkdown(THREAD, META);
		expect(md).toContain('"q": "tote"');
		expect(md).toContain('"from_minor": 49900');
		expect(md).toContain('SpoiledDuckie (shop.test)');
	});

	it('keeps the widget definition the agent offered', () => {
		expect(transcriptMarkdown(THREAD, META)).toContain('"kind": "chips"');
	});

	it('renders a call with no response without inventing one', () => {
		const md = transcriptMarkdown(
			{ messages: [], toolCalls: [{ name: 'quote', request: '{}', response: 'null', at: 'z' }] },
			META
		);
		expect(md).toContain('tool: quote');
		expect(md).not.toContain('Response:');
	});

	it('exports the same entries as JSON', () => {
		const parsed = JSON.parse(transcriptJson(THREAD, META));
		expect(parsed.entries.map((e: any) => e.kind)).toEqual(['message', 'tool', 'message']);
		expect(parsed.entries[1].request).toEqual({ q: 'tote' });
		expect(parsed.driver).toBe('form');
	});
});
