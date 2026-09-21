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
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';
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

/**
 * Tokens live beside the agent's key, on the same volume and for the same
 * reason: **a restart is not a new agent.**
 *
 * Held only in memory, every rebuilt container re-registered — and
 * registration is capped at five an hour per IP (§16.8), so a few rebuilds
 * during a working session left the chat unable to reach the shop at all,
 * reported to the shopper as "the shop is rate-limiting searches". The key
 * already survives a `docker compose down`; the token it was issued for should
 * not be the thing that does not.
 *
 * Not in the session database: that is threads and display, and a bearer token
 * is neither.
 */
const TOKEN_PATH = process.env.CHAT_TOKEN_PATH ?? '/data/agent-tokens.json';

const sessions = new Map<string, Session>();
let loaded = false;

function remember(): void {
	if (loaded) return;
	loaded = true;
	try {
		const stored = JSON.parse(readFileSync(TOKEN_PATH, 'utf8')) as Record<string, Session>;
		for (const [domain, session] of Object.entries(stored)) {
			if (session?.token && session.expiresAt > Date.now()) sessions.set(domain, session);
		}
	} catch {
		// No file, or one this build cannot read. Registering is the fallback and
		// it always works — this cache is an optimisation, never a dependency.
	}
}

function persist(): void {
	try {
		mkdirSync(dirname(TOKEN_PATH), { recursive: true });
		// 0600, like the key: a token is a credential to act as this agent.
		writeFileSync(TOKEN_PATH, JSON.stringify(Object.fromEntries(sessions)), { mode: 0o600 });
	} catch {
		// A read-only volume costs this process nothing but a re-registration on
		// its next start. Never a reason to fail the call in flight.
	}
}

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
	persist();
	return session;
}

async function sessionFor(domain: string): Promise<Session> {
	remember();
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

/** One id per process is enough: nothing here pipelines concurrent calls on
 *  the same connection, so nothing needs the id to correlate a response back
 *  to its request. */
let nextId = 1;

export async function call(
	domain: string,
	tool: string,
	args: Record<string, unknown> = {},
	retry = true
): Promise<ToolResult> {
	const session = await sessionFor(domain);
	const response = await fetch(`${dial(domain)}/agent/mcp`, {
		method: 'POST',
		headers: {
			'content-type': 'application/json',
			authorization: `Bearer ${session.token}`
		},
		body: JSON.stringify({
			jsonrpc: '2.0',
			id: nextId++,
			method: 'tools/call',
			params: { name: tool, arguments: args }
		})
	});
	// A token the shop no longer knows — it restarted, or the token expired
	// early — is worth exactly one fresh registration, not a refusal the
	// shopper has to act on. Once: a shop that refuses the new token too is
	// refusing this agent, and retrying that is how a rate limit is spent.
	if (response.status === 401 && retry) {
		sessions.delete(domain);
		persist();
		await register(domain);
		return call(domain, tool, args, false);
	}
	const body = (await response.json()) as Record<string, any>;
	if (!response.ok) {
		// A refusal before the body was even read as JSON-RPC (auth, rate
		// limit) — this repo's own {"error": {"code", "detail"}} shape, not a
		// JSON-RPC error object.
		throw new ShopError(
			String(body?.error?.code ?? 'refused'),
			String(body?.error?.detail ?? 'The shop refused that.')
		);
	}
	if (body.error) {
		// A JSON-RPC protocol error: malformed request, unknown method, a tool
		// name that doesn't exist. Not a tool refusing — the call never named
		// a real tool to refuse.
		throw new ShopError(
			'invalid-request',
			String(body.error.message ?? 'The shop rejected that call.')
		);
	}
	const result = body.result as Record<string, any>;
	if (result?.isError) {
		const error = (result.structuredContent as Record<string, any>)?.error ?? {};
		throw new ShopError(
			String(error.code ?? 'refused'),
			String(error.detail ?? 'The shop refused that.')
		);
	}
	return (result?.structuredContent ?? {}) as ToolResult;
}
