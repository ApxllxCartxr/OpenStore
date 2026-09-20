/**
 * The smoothness law as **tests, not aspirations** (SPEC §11).
 *
 * Convenience is sidecar-engineered, never agent-hoped. These are the
 * properties the golden replays assert: one Allow-once and one tap per spend,
 * zero re-search, zero re-login, and exact resumed state on both sides.
 *
 * Context loss is a red build.
 */
export type Event =
	| { kind: 'permission'; tool: string; decision: 'allow-once' | 'always' | 'declined' }
	| { kind: 'tap'; order_id: string }
	| { kind: 'tool'; name: string }
	| { kind: 'login' }
	| { kind: 'resume'; order_id: string; thread_id: string };

export class SmoothnessViolation extends Error {}

export function assertOneTapPerSpend(events: Event[]): void {
	const taps = events.filter((e) => e.kind === 'tap');
	const orders = new Set(taps.map((t) => (t as { order_id: string }).order_id));
	if (taps.length !== orders.size) {
		throw new SmoothnessViolation(
			`${taps.length} taps for ${orders.size} order(s). A Consumer asked to tap twice for one ` +
				`purchase has been failed by the sidecar, not by their patience.`
		);
	}
}

export function assertOneAllowOncePerSpend(events: Event[]): void {
	const allows = events.filter(
		(e) => e.kind === 'permission' && e.decision === 'allow-once'
	);
	const taps = events.filter((e) => e.kind === 'tap');
	if (taps.length && allows.length > taps.length + 1) {
		throw new SmoothnessViolation(
			`${allows.length} Allow-once prompts for ${taps.length} spend(s). One per spend is the rule.`
		);
	}
}

export function assertNoReSearchAfterTap(events: Event[]): void {
	const tapAt = events.findIndex((e) => e.kind === 'tap');
	if (tapAt === -1) return;
	const after = events.slice(tapAt);
	const researched = after.some((e) => e.kind === 'tool' && e.name === 'search');
	if (researched) {
		throw new SmoothnessViolation(
			'The chat searched again after the tap. The cart survives the approve page; ' +
				're-searching means it did not.'
		);
	}
}

export function assertNoReLogin(events: Event[]): void {
	if (events.filter((e) => e.kind === 'login').length > 1) {
		throw new SmoothnessViolation('The Consumer was asked to sign in twice for one purchase.');
	}
}

/**
 * Exact resumed state, asserted on **both sides**.
 *
 * The sidecar restores the cart snapshot and the chat restores its thread. If
 * either half is wrong the Consumer comes back to a checkout that is not the
 * one they left, which is the failure the resume token exists to prevent.
 */
export function assertResumedState(
	before: { order_id: string; thread_id: string; lines: string[] },
	after: { order_id: string; thread_id: string; lines: string[] }
): void {
	if (before.order_id !== after.order_id || before.thread_id !== after.thread_id) {
		throw new SmoothnessViolation('Resumed into a different order or thread.');
	}
	if (JSON.stringify(before.lines) !== JSON.stringify(after.lines)) {
		throw new SmoothnessViolation(
			`The basket changed across the approve page: ${before.lines} became ${after.lines}.`
		);
	}
}

export function assertSmooth(events: Event[]): void {
	assertOneTapPerSpend(events);
	assertOneAllowOncePerSpend(events);
	assertNoReSearchAfterTap(events);
	assertNoReLogin(events);
}
