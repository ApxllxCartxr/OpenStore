import { json } from '@sveltejs/kit';
import { sql } from '$lib/db.ts';

export async function GET() {
	try {
		await sql`SELECT 1`;
		return json({ status: 'ok' });
	} catch (error) {
		return json({ status: 'not-ready', reason: String(error) }, { status: 503 });
	}
}
