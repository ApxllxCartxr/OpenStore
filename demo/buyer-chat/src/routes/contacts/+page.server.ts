import { fail } from '@sveltejs/kit';
import { clearSetting, db, getSetting, setSetting } from '$lib/session.ts';
import {
	ContactError,
	connectMcp,
	fetchCard,
	forgetContact,
	saveContact,
	saveGenericContact
} from '$lib/contacts.ts';

const CEILING_KEY = 'spend_ceiling_minor';

/** SSRF-level refusals: the URL itself is refused before anything is asked
 *  of it, so a second attempt with a different parsing strategy would fail
 *  for the identical reason. Everything else is about what came back, and
 *  is worth a second reading — an agent-commerce.json shop and a bare MCP
 *  server can both 404 a request shaped for the other one. */
const URL_LEVEL_REFUSALS = new Set(['not-https', 'not-public', 'metadata-refused', 'blocked']);

export async function load() {
	const contacts = db
		.prepare(`SELECT domain, name, category, protocols, kind, tools, added_at FROM contacts ORDER BY name`)
		.all() as {
		domain: string;
		name: string;
		category: string;
		protocols: string;
		kind: string;
		tools: string;
		added_at: string;
	}[];
	const ceiling = getSetting(CEILING_KEY);
	return {
		contacts: contacts.map((c) => ({
			...c,
			protocols: JSON.parse(c.protocols) as string[],
			toolCount: (JSON.parse(c.tools) as unknown[]).length
		})),
		spendCeilingMinor: ceiling ? Number(ceiling) : null
	};
}

export const actions = {
	/**
	 * One field, two things it might be: an agent-commerce.json card (this
	 * repo's own shops) or a bare MCP server endpoint (everything else —
	 * the same "paste a URL" flow Claude Desktop's own MCP connector uses).
	 * Tried in that order because a card answers its own well-known path
	 * whether or not the pasted URL points there directly, while a generic
	 * server has no such convention to try first.
	 */
	add: async ({ request }) => {
		const form = await request.formData();
		const url = String(form.get('url') ?? '').trim();
		try {
			const card = await fetchCard(url);
			saveContact(card, url);
			return { message: `Added ${card.name} (agent-commerce.json shop).` };
		} catch (cardError) {
			if (!(cardError instanceof ContactError) || URL_LEVEL_REFUSALS.has(cardError.code)) {
				if (cardError instanceof ContactError) {
					return fail(400, { message: cardError.message, code: cardError.code });
				}
				throw cardError;
			}
			try {
				const server = await connectMcp(url);
				saveGenericContact(server);
				return {
					message: `Connected to ${server.name} — ${server.tools.length} tool${server.tools.length === 1 ? '' : 's'} offered.`
				};
			} catch (mcpError) {
				// Neither reading worked. The card's own refusal is usually the
				// more informative one — a real shop that briefly 500'd reads
				// better than "no tools" from the same failed fetch reinterpreted.
				if (mcpError instanceof ContactError) {
					return fail(400, { message: mcpError.message, code: mcpError.code });
				}
				throw mcpError;
			}
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
