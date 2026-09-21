/**
 * The air-gap, as a build break rather than a sentence.
 *
 * The chat holds threads and display. Destination and Contact Point live in
 * memory for one checkout and are posted to the sidecar — an agent that
 * persists them has become a place PII leaks from, which is the thing the
 * air-gap exists to prevent.
 */
import { describe, expect, it } from 'vitest';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { FORBIDDEN_COLUMNS, schemaColumns } from '../src/lib/session.ts';

describe('the session database', () => {
	it('has no column that could hold PII or money', () => {
		const columns = schemaColumns().map((c) => c.split('.')[1]!);
		const planted = columns.filter((c) => FORBIDDEN_COLUMNS.includes(c));
		expect(planted).toEqual([]);
	});

	it('holds threads, messages, tool calls, contacts, order views and settings — and nothing else', () => {
		// `settings` is a Consumer preference store (currently: an advisory
		// spend ceiling), not money state or PII — a preference is not a total,
		// a price or a payment credential, and FORBIDDEN_COLUMNS above still
		// refuses it a real one under any name.
		const tables = [...new Set(schemaColumns().map((c) => c.split('.')[0]))].sort();
		expect(tables).toEqual(['contacts', 'messages', 'order_views', 'settings', 'threads', 'tool_calls']);
	});

	it('keeps only status and reason on an order, never a total', () => {
		const orderColumns = schemaColumns()
			.filter((c) => c.startsWith('order_views.'))
			.map((c) => c.split('.')[1]);
		expect(orderColumns).not.toContain('total_minor');
		expect(orderColumns).not.toContain('destination');
	});
});

describe('no source file writes PII to the session DB', () => {
	const SRC = new URL('../src', import.meta.url).pathname;
	const files = (function walk(dir: string): string[] {
		return readdirSync(dir).flatMap((entry) => {
			const path = join(dir, entry);
			return statSync(path).isDirectory() ? walk(path) : [path];
		});
	})(SRC);

	it('never INSERTs a destination or a contact', () => {
		const offenders = files.filter((path) => {
			const source = readFileSync(path, 'utf8');
			return /INSERT INTO \w+[^;]*\b(destination|payer_handle)\b/i.test(source);
		});
		expect(offenders).toEqual([]);
	});

	it('the only `contact` in the schema is a shop contact, not a person', () => {
		// `contacts` is the Consumer's list of *shops*. The word is reused and
		// the distinction matters, so it is asserted rather than assumed.
		const schema = readFileSync(new URL('../src/lib/session.ts', import.meta.url).pathname, 'utf8');
		expect(schema).toMatch(/CREATE TABLE IF NOT EXISTS contacts/);
		expect(schema).not.toMatch(/contact_point|consumer_email|consumer_phone/i);
	});
});
