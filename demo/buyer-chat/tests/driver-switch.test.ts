/**
 * The model switcher's selection logic: which drivers this process can
 * actually offer, and which one a Consumer's choice resolves to.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const ENV_KEYS = ['CHAT_MODEL_DRIVER', 'OPENROUTER_API_KEY', 'ANTHROPIC_API_KEY', 'OLLAMA_MODEL'] as const;
const saved: Record<string, string | undefined> = {};

beforeEach(() => {
	for (const key of ENV_KEYS) {
		saved[key] = process.env[key];
		delete process.env[key];
	}
});

afterEach(() => {
	for (const key of ENV_KEYS) {
		if (saved[key] === undefined) delete process.env[key];
		else process.env[key] = saved[key];
	}
});

/** A fresh module instance per test: `byChoice` is a module-level cache, and
 *  each test sets its own env combination before importing. */
async function driverModule() {
	vi.resetModules();
	return import('../src/lib/model/driver.ts');
}

describe('availableDrivers', () => {
	it('offers only scripted when nothing else is configured', async () => {
		const { availableDrivers } = await driverModule();
		const configured = availableDrivers().filter((d) => d.configured);
		expect(configured.map((d) => d.id)).toEqual(['scripted']);
	});

	it('offers a driver once its key or model is set', async () => {
		process.env.OPENROUTER_API_KEY = 'k';
		process.env.ANTHROPIC_API_KEY = 'k';
		process.env.OLLAMA_MODEL = 'llama3';
		const { availableDrivers } = await driverModule();
		const configured = availableDrivers()
			.filter((d) => d.configured)
			.map((d) => d.id)
			.sort();
		expect(configured).toEqual(['anthropic', 'ollama', 'openrouter', 'scripted'].sort());
	});
});

describe('driverFor', () => {
	it('falls back to the env default when no override is given', async () => {
		const { driverFor } = await driverModule();
		expect(driverFor(null).name).toBe('scripted');
	});

	it('falls back to the env default when the override names an unconfigured driver', async () => {
		const { driverFor } = await driverModule();
		// openrouter is not configured in this test's env — asking for it must
		// not silently fail the request, only decline to honour the choice.
		expect(driverFor('openrouter').name).toBe('scripted');
	});

	it('honours an override that names a configured driver', async () => {
		process.env.ANTHROPIC_API_KEY = 'k';
		const { driverFor } = await driverModule();
		expect(driverFor('anthropic').name).toBe('anthropic');
	});

	it('returns the same instance for the same choice — the scripted driver keeps its place', async () => {
		const { driverFor } = await driverModule();
		expect(driverFor(null)).toBe(driverFor(null));
	});
});
