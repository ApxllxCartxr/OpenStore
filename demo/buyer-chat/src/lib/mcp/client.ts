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

/** A shop that hangs — a bad deploy, a network partition — used to hang this
 *  call forever with it: nothing here ever timed out. This repo's own shops
 *  are more trusted than a generic Consumer-pasted server, but "more
 *  trusted" is not "never fails to answer". */
const SHOP_FETCH_TIMEOUT_MS = 15_000;

async function register(domain: string): Promise<Session> {
	const controller = new AbortController();
	const timer = setTimeout(() => controller.abort(), SHOP_FETCH_TIMEOUT_MS);
	let response: Response;
	try {
		response = await fetch(`${dial(domain)}/agent/register`, {
			method: 'POST',
			headers: { 'content-type': 'application/json' },
			body: JSON.stringify({ name: 'OpenStore demo chat', profile_url: profileUrl() }),
			signal: controller.signal
		});
	} catch {
		throw new ShopError('unreachable', `${domain} did not answer in time.`);
	} finally {
		clearTimeout(timer);
	}
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
	const controller = new AbortController();
	const timer = setTimeout(() => controller.abort(), SHOP_FETCH_TIMEOUT_MS);
	let response: Response;
	try {
		response = await fetch(`${dial(domain)}/agent/mcp`, {
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
			}),
			signal: controller.signal
		});
	} catch {
		throw new ShopError('unreachable', `${domain} did not answer in time.`);
	} finally {
		clearTimeout(timer);
	}
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

// ── Generic MCP — any server, not just this repo's own shops ───────────────
//
// The self-registration above is this repo's own admission scheme
// (ADR-0012): a published Agent Profile, a fetched key, a bearer token. A
// server that isn't one of this repo's shops has no reason to speak it —
// most public MCP servers take no credential at all, or one this repo has no
// story for yet (OAuth 2.1). So the generic path sends no Authorization
// header, and a server that answers 401 gets a refusal that says so plainly
// rather than a client that pretends to have tried harder than it did.

export type ToolDescriptor = {
	name: string;
	description: string;
	inputSchema: Record<string, unknown>;
	annotations: {
		readOnlyHint?: boolean;
		destructiveHint?: boolean;
		moneyPathHint?: boolean;
		[key: string]: unknown;
	};
};

// A generic server is Consumer-pasted and untrusted the same way a card's URL
// is (`contacts.ts`'s own FETCH_TIMEOUT_MS/MAX_CARD_BYTES) — but this path is
// hit on every tool call, not once at add time, so a slow or oversized answer
// here is a live request hanging, not just a contact that failed to add.
const MCP_FETCH_TIMEOUT_MS = 10_000;
const MAX_MCP_RESPONSE_BYTES = 256 * 1024;

// Streamable HTTP sessions (MCP 2025-06-18): a stateful server returns
// `Mcp-Session-Id` on `initialize` and expects it back thereafter.
const mcpSessions = new Map<string, string>();

function parseSseBody(text: string): any {
	// One SSE stream can carry several events; the JSON-RPC response is the
	// last `data:` payload that parses as one. Anything else (pings,
	// progress notices, `[DONE]`) is skipped, not fatal.
	let candidate: any = null;
	for (const event of text.split(/\r?\n\r?\n/)) {
		const data = event
			.split(/\r?\n/)
			.filter((line) => line.startsWith('data:'))
			.map((line) => line.slice(5).trimStart())
			.join('\n');
		if (!data || data === '[DONE]') continue;
		try {
			const parsed = JSON.parse(data);
			if (parsed && typeof parsed === 'object' && ('result' in parsed || 'error' in parsed)) {
				candidate = parsed;
			}
		} catch {
			// Not JSON — keep looking at the next event.
		}
	}
	if (candidate) return candidate;
	// Some servers send a bare JSON body with an SSE content-type.
	return JSON.parse(text);
}

async function rpc(
	endpoint: string,
	method: string,
	params: Record<string, unknown> = {},
	fetcher: typeof fetch = fetch
): Promise<any> {
	const controller = new AbortController();
	const timer = setTimeout(() => controller.abort(), MCP_FETCH_TIMEOUT_MS);
	const headers: Record<string, string> = {
		'content-type': 'application/json',
		// Streamable HTTP requires both; without it strict servers 406.
		accept: 'application/json, text/event-stream'
	};
	const session = mcpSessions.get(endpoint);
	if (session) headers['mcp-session-id'] = session;
	let response: Response;
	try {
		response = await fetcher(endpoint, {
			method: 'POST',
			headers,
			body: JSON.stringify({ jsonrpc: '2.0', id: nextId++, method, params }),
			signal: controller.signal
		});
	} catch {
		throw new ShopError('unreachable', `${new URL(endpoint).hostname} did not answer in time.`);
	} finally {
		clearTimeout(timer);
	}
	const returned = response.headers?.get?.('mcp-session-id');
	if (returned) mcpSessions.set(endpoint, returned);
	if (response.status === 401 || response.status === 403) {
		throw new ShopError(
			'auth-required',
			`${new URL(endpoint).hostname} needs a credential this chat does not have a way to provide yet.`
		);
	}
	const text = await response.text();
	if (text.length > MAX_MCP_RESPONSE_BYTES) {
		throw new ShopError('too-large', `${new URL(endpoint).hostname} answered with more than this chat will read.`);
	}
	// A notification acknowledgement (202, empty body) never reaches here
	// through this path, but a server may also 202 a regular call with an
	// empty stream — that is not a result.
	if (!text) {
		throw new ShopError('not-mcp', `${new URL(endpoint).hostname} did not answer with JSON-RPC.`);
	}
	let body: any;
	try {
		const contentType = response.headers?.get?.('content-type') ?? '';
		body = contentType.includes('text/event-stream') ? parseSseBody(text) : JSON.parse(text);
	} catch {
		throw new ShopError('not-mcp', `${new URL(endpoint).hostname} did not answer with JSON-RPC.`);
	}
	if (!response.ok || body.error) {
		throw new ShopError(
			'refused',
			String(body?.error?.message ?? `${new URL(endpoint).hostname} answered ${response.status}.`)
		);
	}
	return body.result;
}

async function notify(
	endpoint: string,
	method: string,
	params: Record<string, unknown> = {},
	fetcher: typeof fetch = fetch
): Promise<void> {
	// JSON-RPC notification: no id, no response body expected (202).
	// Best-effort: servers that do not need it answer 404/405, which is fine.
	const controller = new AbortController();
	const timer = setTimeout(() => controller.abort(), MCP_FETCH_TIMEOUT_MS);
	try {
		const headers: Record<string, string> = {
			'content-type': 'application/json',
			accept: 'application/json, text/event-stream'
		};
		const session = mcpSessions.get(endpoint);
		if (session) headers['mcp-session-id'] = session;
		const response = await fetcher(endpoint, {
			method: 'POST',
			headers,
			body: JSON.stringify({ jsonrpc: '2.0', method, params }),
			signal: controller.signal
		});
		const returned = response.headers?.get?.('mcp-session-id');
		if (returned) mcpSessions.set(endpoint, returned);
		await response.text().catch(() => '');
	} catch {
		// Notifications never fail the flow that sent them.
	} finally {
		clearTimeout(timer);
	}
}

/** The handshake, and what the server says about itself — used once, at
 *  add-contact time, to name the shop something better than its own
 *  hostname when the server bothers to say who it is. */
export async function initializeGeneric(
	endpoint: string,
	fetcher: typeof fetch = fetch
): Promise<{ name: string | null }> {
	const result = await rpc(
		endpoint,
		'initialize',
		{ protocolVersion: '2025-06-18', capabilities: {}, clientInfo: { name: 'Miro', version: '1' } },
		fetcher
	);
	// Stateful servers expect this before `tools/list`; stateless ones ignore
	// it. Best-effort so a 404 here never fails the add.
	await notify(endpoint, 'notifications/initialized', {}, fetcher);
	const name = result?.serverInfo?.name;
	return { name: typeof name === 'string' && name ? name : null };
}

export async function listToolsGeneric(
	endpoint: string,
	fetcher: typeof fetch = fetch
): Promise<ToolDescriptor[]> {
	const result = await rpc(endpoint, 'tools/list', {}, fetcher);
	const tools = Array.isArray(result?.tools) ? result.tools : [];
	return tools
		.filter((t: any) => typeof t?.name === 'string')
		.map((t: any) => ({
			name: String(t.name),
			description: typeof t.description === 'string' ? t.description : '',
			inputSchema: typeof t.inputSchema === 'object' && t.inputSchema ? t.inputSchema : { type: 'object' },
			annotations: typeof t.annotations === 'object' && t.annotations ? t.annotations : {}
		}));
}

export async function callGeneric(
	endpoint: string,
	tool: string,
	args: Record<string, unknown> = {},
	fetcher: typeof fetch = fetch
): Promise<ToolResult> {
	const result = await rpc(endpoint, 'tools/call', { name: tool, arguments: args }, fetcher);
	if (result?.isError) {
		const text = Array.isArray(result.content) ? result.content.map((c: any) => c?.text ?? '').join(' ') : '';
		throw new ShopError('refused', text.trim() || 'That call was refused.');
	}
	// `structuredContent` is optional in the spec; a server that only returns
	// `content` still gets something usable rather than an empty object —
	// text is tried as JSON first (many servers just stringify their result),
	// and kept as plain text otherwise.
	if (result?.structuredContent && typeof result.structuredContent === 'object') {
		return result.structuredContent as ToolResult;
	}
	const text = Array.isArray(result?.content)
		? result.content
				.map((c: any) => (typeof c?.text === 'string' ? c.text : ''))
				.filter(Boolean)
				.join('\n')
		: '';
	try {
		const parsed = JSON.parse(text);
		if (parsed && typeof parsed === 'object') return parsed as ToolResult;
	} catch {
		// Not JSON — fall through to the plain-text wrapper below.
	}
	return { text };
}
