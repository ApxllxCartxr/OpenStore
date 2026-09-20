import { fail } from '@sveltejs/kit';
import { db } from '$lib/session.ts';
import { ContactError, fetchCard, forgetContact, saveContact } from '$lib/contacts.ts';

export async function load() {
	const contacts = db
		.prepare(`SELECT domain, name, category, protocols, added_at FROM contacts ORDER BY name`)
		.all() as { domain: string; name: string; category: string; protocols: string; added_at: string }[];
	return {
		contacts: contacts.map((c) => ({ ...c, protocols: JSON.parse(c.protocols) as string[] }))
	};
}

export const actions = {
	add: async ({ request }) => {
		const form = await request.formData();
		const url = String(form.get('url') ?? '').trim();
		try {
			const card = await fetchCard(url);
			saveContact(card, url);
			return { message: `Added ${card.name}.` };
		} catch (error) {
			// Every failure names its reason: unknown URL, tampered card, dead
			// sidecar. A generic "could not add" tells the Consumer nothing they
			// can act on.
			if (error instanceof ContactError) return fail(400, { message: error.message, code: error.code });
			throw error;
		}
	},
	forget: async ({ request }) => {
		const form = await request.formData();
		forgetContact(String(form.get('domain') ?? ''));
		// Local bookmark only. Server revocation is separate and authoritative.
		return { message: 'Removed from this chat. The shop still knows this agent.' };
	}
};
