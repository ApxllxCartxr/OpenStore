/**
 * The transcript, as a file.
 *
 * A GET so the button in the topbar is a plain download link: no state, no
 * action, nothing to undo. Markdown by default because the point is that a
 * Consumer can read what their agent did; `?format=json` for anything that
 * needs to parse it.
 */
import { error } from '@sveltejs/kit';
import type { RequestHandler } from './$types';
import { THREAD, db, thread } from '$lib/session.ts';
import { driverFromEnv } from '$lib/model/driver.ts';
import { transcriptJson, transcriptMarkdown } from '$lib/transcript.ts';

export const GET: RequestHandler = ({ url }) => {
	const format = url.searchParams.get('format') ?? 'md';
	if (format !== 'md' && format !== 'json') {
		error(400, `Unknown transcript format "${format}" — use md or json.`);
	}
	const shops = db.prepare(`SELECT domain, name FROM contacts ORDER BY added_at ASC`).all() as {
		domain: string;
		name: string;
	}[];
	const exportedAt = new Date().toISOString();
	const meta = { shops, driver: driverFromEnv().name, exportedAt };
	const state = thread(THREAD);
	const body =
		format === 'json' ? transcriptJson(state, meta) : transcriptMarkdown(state, meta);
	// The date, not the instant: a second file exported the same day should
	// overwrite the first in the download folder rather than pile up.
	const name = `miro-transcript-${exportedAt.slice(0, 10)}.${format}`;
	return new Response(body, {
		headers: {
			'content-type':
				format === 'json' ? 'application/json; charset=utf-8' : 'text/markdown; charset=utf-8',
			'content-disposition': `attachment; filename="${name}"`,
			'cache-control': 'no-store'
		}
	});
};
