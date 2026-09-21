/**
 * The settings store: a Consumer preference, not money state. Currently
 * holds one thing — an advisory spend ceiling — but the get/set/clear shape
 * is generic on purpose, so a second preference is a new key, not a new
 * table.
 */
import { describe, expect, it } from 'vitest';
import { clearSetting, getSetting, setSetting } from '../src/lib/session.ts';

describe('settings', () => {
	it('is unset until something is saved', () => {
		expect(getSetting('never-set')).toBeNull();
	});

	it('saves and reads back a value', () => {
		setSetting('spend_ceiling_minor', '200000');
		expect(getSetting('spend_ceiling_minor')).toBe('200000');
	});

	it('overwrites rather than duplicating on a second save', () => {
		setSetting('spend_ceiling_minor', '200000');
		setSetting('spend_ceiling_minor', '500000');
		expect(getSetting('spend_ceiling_minor')).toBe('500000');
	});

	it('clears back to unset', () => {
		setSetting('spend_ceiling_minor', '200000');
		clearSetting('spend_ceiling_minor');
		expect(getSetting('spend_ceiling_minor')).toBeNull();
	});
});
