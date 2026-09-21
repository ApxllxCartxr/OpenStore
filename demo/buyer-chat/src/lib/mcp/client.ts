/**
 * The chat's MCP client — how the agent actually reaches a shop.
 *
 * `src/lib/mcp/` was an empty directory: the chat could add a shop and then do
 * nothing with it, because there was no client and the sidecar's tool surface
 * ran nothing either. Both halves are real now.
 *
 * **Admission is self-registration** (ADR-0012): the chat publishes an Agent
 * Profile, the sidecar fetches it, derives the `agent_id` from the key, and
 * issues a token. No Merchant action is needed, so no Merchant action is asked
 * for. The token is cached per shop until it expires.
 *
 * **This client cannot spend.** The scopes it is issued stop at `confirm`, and
 * `place-order` returns a link for the Consumer to approve on the shop's own
 * origin. Nothing here holds a payment credential.
 */
import { agentProfile, loadOrCreate } from '../identity/keys.ts';

export type ToolResult = Record<string, unknown>;

export class ShopError extends Error {
	constructor(
		readonly code: string,
		message: string
	) {
		super(message);
	}
}

type Session = { token: string; expiresAt: number; agentId: string };

const sessions = new Map<string, Session>();

/** Where the chat tells the shop to fetch its profile from.
 *
 *  Inside compose the sidecar cannot resolve `chat.localhost` — that name means
 *  the container's own loopback there (§10.1) — so it is given the service
 *  address it can actually reach. */
function profileUrl(): string {
	const host = process.env.CHAT_INTERNAL_ORIGIN ?? 'http://buyer-chat:3001';
	return `${host.replace(/\/$/, '')}/.well-known/agent-profile.json`;
}

/** The origin the chat dials: the shop's own domain, exactly as its card names
 *  it.
 *
 *  Not a proxy address with a rewritten `Host` header — that was tried and the
 *  response body came back empty, and it would also mean the shop answering
 *  under a name it does not know it has. In compose the demo's public names
 *  resolve to the edge on the shared network; in production they resolve the
 *  way any domain does.
 *
 *  Plain http only for a host the demo has explicitly named, mirroring the
 *  card fetcher's one exception — every other shop is dialled over HTTPS. */
function dial(domain: string): string {
	const devHosts = (process.env.OPENSTORE_DEV_PROFILE_HOSTS ?? '')
		.split(',')
		.map((h) => h.trim().split(':')[0])
		.filter(Boolean);
	const scheme = devHosts.includes(domain) ? 'http' : 'https';
	return `${scheme}://${domain}`;
}

async function register(domain: string): Promise<Session> {
	const response = await fetch(`${dial(domain)}/agent/register`, {
		method: 'POST',
		headers: { 'content-type': 'application/json' },
		body: JSON.stringify({ name: 'OpenStore demo chat', profile_url: profileUrl() })
	});
	const body = (await response.json()) as Record<string, any>;
	if (!response.ok) {
		throw new ShopError(
			String(body?.error?.code ?? 'refused'),
			String(body?.error?.detail ?? `${domain} refused to admit this agent.`)
		);
	}
	const session: Session = {
		token: String(body.access_token),
		agentId: String(body.agent_id),
		expiresAt: Date.parse(String(body.expires_at)) || Date.now() + 60_000
	};
	sessions.set(domain, session);
	return session;
}

async function sessionFor(domain: string): Promise<Session> {
	const held = sessions.get(domain);
	// A minute of headroom: a token that expires mid-call fails the call rather
	// than the conversation.
	if (held && held.expiresAt - 60_000 > Date.now()) return held;
	return register(domain);
}

/** Our own identity, so the UI can show who the shop knows us as. */
export async function agentId(): Promise<string> {
	const identity = await loadOrCreate();
	return agentProfile(identity, '').jwks.keys[0]?.kid ?? 'chat-k1';
}

export async function call(
	domain: string,
	tool: string,
	args: Record<string, unknown> = {}
): Promise<ToolResult> {
	const session = await sessionFor(domain);
	const response = await fetch(`${dial(domain)}/agent/mcp`, {
		method: 'POST',
		headers: {
			'content-type': 'application/json',
			authorization: `Bearer ${session.token}`
		},
		body: JSON.stringify({ tool, input: args })
	});
	const body = (await response.json()) as Record<string, any>;
	if (!response.ok) {
		// The shop's own reason, verbatim. An agent that rewrites a refusal into
		// "something went wrong" has destroyed the only useful part of it.
		throw new ShopError(
			String(body?.error?.code ?? 'refused'),
			String(body?.error?.detail ?? 'The shop refused that.')
		);
	}
	return (body.result ?? {}) as ToolResult;
}
