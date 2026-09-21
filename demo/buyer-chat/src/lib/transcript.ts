/**
 * The thread, exported as a file the Consumer keeps.
 *
 * Same rule as the page: nothing here is computed or summarised. Every
 * message is the text that was said, every tool call is the exact request and
 * the exact response JSON — the card on the page, unfolded. A summary in an
 * export would be a second account of what happened, and the one the Consumer
 * took away would be the wrong one.
 */

export type TranscriptThread = {
	messages: { id: number; role: string; text: string; widget: string | null; at: string }[];
	toolCalls: { shop: string; name: string; request: string; response: string | null; at: string }[];
};

export type TranscriptMeta = {
	/** Every shop this thread has been introduced to. */
	shops: { domain: string; name: string }[];
	driver: string;
	exportedAt: string;
};

type Entry =
	| { kind: 'message'; role: string; text: string; widget: unknown; at: string }
	| { kind: 'tool'; shop: string; name: string; request: unknown; response: unknown; at: string };

/** Messages and tool calls in the order they happened — the same interleave
 *  the page renders, so the file reads as the conversation did. */
function entries(thread: TranscriptThread): Entry[] {
	const items: Entry[] = [
		...thread.messages.map((m): Entry => ({
			kind: 'message',
			role: m.role,
			text: m.text,
			widget: m.widget ? JSON.parse(m.widget) : null,
			at: m.at
		})),
		...thread.toolCalls.map((c): Entry => ({
			kind: 'tool',
			shop: c.shop,
			name: c.name,
			request: JSON.parse(c.request),
			response: c.response ? JSON.parse(c.response) : null,
			at: c.at
		}))
	];
	return items.sort((a, b) => (a.at < b.at ? -1 : a.at > b.at ? 1 : 0));
}

/** The whole thread as JSON: parsed request and response bodies rather than
 *  the strings the database keeps them in, so the file is readable by a tool. */
export function transcriptJson(thread: TranscriptThread, meta: TranscriptMeta): string {
	return JSON.stringify({ ...meta, entries: entries(thread) }, null, 2);
}

function fence(value: unknown): string {
	return '```json\n' + JSON.stringify(value, null, 2) + '\n```';
}

export function transcriptMarkdown(thread: TranscriptThread, meta: TranscriptMeta): string {
	const out: string[] = [
		'# Miro — chat transcript',
		'',
		`- Exported: ${meta.exportedAt}`,
		`- Shops: ${meta.shops.length ? meta.shops.map((s) => `${s.name} (${s.domain})`).join(', ') : 'none'}`,
		`- Driver: ${meta.driver}`,
		''
	];
	for (const entry of entries(thread)) {
		if (entry.kind === 'message') {
			out.push(`### ${entry.role === 'agent' ? 'Miro' : 'You'} · ${entry.at}`, '');
			if (entry.text) out.push(entry.text, '');
			// The interface the agent offered, not what was answered into it:
			// an answer is the next message or the next tool call, and both are
			// already in this file under their own timestamps.
			if (entry.widget) out.push('Offered:', fence(entry.widget), '');
		} else {
			out.push(
				`### tool: ${entry.name}${entry.shop ? ` @ ${entry.shop}` : ''} · ${entry.at}`,
				'',
				'Request:',
				fence(entry.request),
				''
			);
			if (entry.response !== null) out.push('Response:', fence(entry.response), '');
		}
	}
	return out.join('\n');
}
