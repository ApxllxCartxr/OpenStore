/**
 * The session DB: **threads and display only.**
 *
 * No carts, no money state, no Consumer PII. The cart lives in the sidecar and
 * chat cart-ops are calls, not local state — an agent that keeps its own basket
 * has two baskets, and the one it shows is the wrong one.
 *
 * Destination and Contact Point are collected in C3 and held **in memory for
 * the length of that checkout only**: posted to the sidecar, never written
 * here, never logged, dropped at thread end. An agent that persists them has
 * become a place PII leaks from, which is the thing the air-gap exists to
 * prevent.
 *
 * `tests/no-pii-in-session.test.ts` plants a write of each and fails the build.
 * A claim about not storing PII with no test behind it is just a sentence.
 */
import { DatabaseSync } from 'node:sqlite';

const PATH = process.env.CHAT_DATABASE_PATH ?? ':memory:';

export const db = new DatabaseSync(PATH);

db.exec(`
	CREATE TABLE IF NOT EXISTS threads (
	  id          TEXT PRIMARY KEY,
	  title       TEXT NOT NULL DEFAULT 'New chat',
	  created_at  TEXT NOT NULL
	);
	-- The widget column is the interface the agent offered with a message: a
	-- form's labels and field names, a choice's options. Definitions only:
	-- what the Consumer types into one is their own message, and what they
	-- choose is a tool call. No answers are kept here.
	CREATE TABLE IF NOT EXISTS messages (
	  id        INTEGER PRIMARY KEY AUTOINCREMENT,
	  thread_id TEXT NOT NULL REFERENCES threads(id),
	  role      TEXT NOT NULL CHECK (role IN ('consumer','agent')),
	  text      TEXT NOT NULL,
	  widget    TEXT,
	  at        TEXT NOT NULL
	);
	-- Tool calls are kept for display: the card expands to the exact request
	-- JSON, which is the difference between a tool call and a black box.
	CREATE TABLE IF NOT EXISTS tool_calls (
	  id        INTEGER PRIMARY KEY AUTOINCREMENT,
	  thread_id TEXT NOT NULL REFERENCES threads(id),
	  name      TEXT NOT NULL,
	  request   TEXT NOT NULL,
	  response  TEXT,
	  at        TEXT NOT NULL
	);
	-- Contacts: a shop this Consumer has added, with its pinned JWKS (TOFU).
	CREATE TABLE IF NOT EXISTS contacts (
	  domain      TEXT PRIMARY KEY,
	  name        TEXT NOT NULL,
	  category    TEXT NOT NULL DEFAULT '',
	  card_url    TEXT NOT NULL,
	  jwks_url    TEXT NOT NULL DEFAULT '',
	  jwks        TEXT NOT NULL,
	  protocols   TEXT NOT NULL DEFAULT '[]',
	  added_at    TEXT NOT NULL
	);
	-- What the sidecar told us about an order. Status and reason code only —
	-- never Destination, never Contact Point, never a line-level price the
	-- Merchant did not sign.
	CREATE TABLE IF NOT EXISTS order_views (
	  order_id    TEXT PRIMARY KEY,
	  thread_id   TEXT NOT NULL,
	  domain      TEXT NOT NULL,
	  status      TEXT NOT NULL,
	  reason_code TEXT,
	  receipt_id  TEXT,
	  updated_at  TEXT NOT NULL
	);
`);

// `CREATE TABLE IF NOT EXISTS` does nothing to a table that already exists, so
// a column added later never reaches a database that predates it — and the
// chat's DB lives in a volume that outlives any rebuild. Add it here or the
// first contact write after an upgrade fails on a column that is not there.
const contactColumns = (db.prepare(`PRAGMA table_info(contacts)`).all() as { name: string }[]).map(
	(column) => column.name
);
if (!contactColumns.includes('jwks_url')) {
	db.exec(`ALTER TABLE contacts ADD COLUMN jwks_url TEXT NOT NULL DEFAULT ''`);
}

const messageColumns = (db.prepare(`PRAGMA table_info(messages)`).all() as { name: string }[]).map(
	(column) => column.name
);
if (!messageColumns.includes('widget')) {
	db.exec(`ALTER TABLE messages ADD COLUMN widget TEXT`);
}

/**
 * Column names this database may never grow.
 *
 * Checked by a test rather than remembered. The air-gap is a property of the
 * schema, not of anybody's discipline.
 */
export const FORBIDDEN_COLUMNS = [
	'destination',
	'contact',
	'email',
	'phone',
	'line1',
	'postal_code',
	'payer_handle',
	'card_number',
	'total_minor',
	'price_minor'
];

export function schemaColumns(): string[] {
	const tables = db
		.prepare(
			// `sqlite_sequence` and friends are SQLite's own bookkeeping, not
			// tables this app declared. Counting them would make the schema
			// assertion depend on whether anything uses AUTOINCREMENT.
			`SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'`
		)
		.all() as { name: string }[];
	return tables.flatMap((table) =>
		(db.prepare(`PRAGMA table_info(${table.name})`).all() as { name: string }[]).map(
			(column) => `${table.name}.${column.name}`
		)
	);
}

/** The one thread this demo keeps. Shared so the export endpoint reads the
 *  same rows the page renders, rather than a second guess at the id. */
export const THREAD = 'thread_demo';

export function createThread(id: string, title = 'New chat'): void {
	db.prepare(`INSERT OR IGNORE INTO threads (id, title, created_at) VALUES (?, ?, ?)`).run(
		id,
		title,
		new Date().toISOString()
	);
}

export function addMessage(
	threadId: string,
	role: 'consumer' | 'agent',
	text: string,
	widget: unknown = null
): void {
	db.prepare(
		`INSERT INTO messages (thread_id, role, text, widget, at) VALUES (?, ?, ?, ?, ?)`
	).run(threadId, role, text, widget ? JSON.stringify(widget) : null, new Date().toISOString());
}

/** One message's widget definition, read back from the database rather than
 *  from the browser. A submission posts values; the shape they go into is the
 *  one this chat stored when it offered the widget. */
export function widgetFor(threadId: string, messageId: number): string | null {
	const row = db
		.prepare(`SELECT widget FROM messages WHERE id = ? AND thread_id = ?`)
		.get(messageId, threadId) as { widget: string | null } | undefined;
	return row?.widget ?? null;
}

export function recordToolCall(
	threadId: string,
	name: string,
	request: unknown,
	response: unknown
): void {
	db.prepare(
		`INSERT INTO tool_calls (thread_id, name, request, response, at) VALUES (?, ?, ?, ?, ?)`
	).run(threadId, name, JSON.stringify(request), JSON.stringify(response ?? null), new Date().toISOString());
}

export function thread(threadId: string) {
	return {
		messages: db
			.prepare(`SELECT id, role, text, widget, at FROM messages WHERE thread_id = ? ORDER BY id`)
			.all(threadId) as {
			id: number;
			role: string;
			text: string;
			widget: string | null;
			at: string;
		}[],
		toolCalls: db
			.prepare(`SELECT name, request, response, at FROM tool_calls WHERE thread_id = ? ORDER BY id`)
			.all(threadId) as { name: string; request: string; response: string; at: string }[]
	};
}
