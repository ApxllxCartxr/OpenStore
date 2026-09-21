import { fail } from '@sveltejs/kit';
import { clearSetting, db, getSetting, setSetting } from '$lib/session.ts';
import { ContactError, fetchCard, forgetContact, saveContact } from '$lib/contacts.ts';

const CEILING_KEY = 'spend_ceiling_minor';

export async function load() {
	const contacts = db
		.prepare(`SELECT domain, name, category, protocols, added_at FROM contacts ORDER BY name`)
		.all() as { domain: string; name: string; category: string; protocols: string; added_at: string }[];
	const ceiling = getSetting(CEILING_KEY);
	return {
		contacts: contacts.map((c) => ({ ...c, protocols: JSON.parse(c.protocols) as string[] })),
		spendCeilingMinor: ceiling ? Number(ceiling) : null
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
	},

	/**
	 * An advisory ceiling, not a limit anything enforces. Set here, read only
	 * to add a line to the place-order consent prompt when a checkout's total
	 * crosses it — this chat holds no payment credential and no rail-level
	 * mandate exists to enforce a number against (ADR-0008, ADR-0024). Saying
	 * so is the point: a "budget" that quietly implied more than that would be
	 * the thing worth flagging, not the thing worth shipping quietly.
	 */
	ceiling: async ({ request }) => {
		const form = await request.formData();
		const raw = String(form.get('rupees') ?? '').trim();
		if (!raw) {
			clearSetting(CEILING_KEY);
			return { message: 'Ceiling cleared. I will not flag any total.' };
		}
		const rupees = Number(raw);
		if (!Number.isFinite(rupees) || rupees <= 0) {
			return fail(400, { message: 'That needs to be a positive number of rupees.' });
		}
		setSetting(CEILING_KEY, String(Math.round(rupees * 100)));
		return { message: `I will flag any checkout over ₹${rupees.toFixed(2)} before you approve it.` };
	}
};
