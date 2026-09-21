/**
 * The Agent Profile this chat publishes (A4c).
 *
 * `agentProfile()` was written, documented as the thing this chat publishes,
 * and never routed — so the sidecar's `/agent/register` had nothing to fetch
 * and no agent could be admitted at all. The key is the one from
 * `CHAT_KEY_PATH`, so a rebuilt container re-registers as the same agent.
 *
 * It **admits**; it never authorizes a spend: a name, a contact, and a public
 * key the sidecar can verify signatures against. Nothing here is secret.
 */
import { json } from '@sveltejs/kit';
import { agentProfile, loadOrCreate } from '$lib/identity/keys.ts';
import type { RequestHandler } from './$types';

export const GET: RequestHandler = async ({ url }) => {
	const identity = await loadOrCreate();
	return json(agentProfile(identity, url.origin));
};
