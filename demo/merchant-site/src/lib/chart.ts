/**
 * Chart geometry, kept out of the markup.
 *
 * The shop's admin and the sidecar's `/agentic` console draw the same shapes to
 * the same specs, and they cannot share a module across that boundary, so the
 * specs live written down in both. Changing one without the other is how two
 * dashboards start disagreeing about what a bar looks like.
 *
 * Specs: bars cap at 24px rather than filling their band, the leftover is air,
 * the data-end is rounded 4px and the baseline end is square.
 */

export const BAR_MAX = 24;
const BAR_R = 4;

/** Round an axis top up to a number a person would have picked. */
export function niceCeiling(value: number): number {
	if (value <= 0) return 1;
	const step = 10 ** (String(Math.trunc(value)).length - 1);
	for (const multiple of [1, 2, 2.5, 5, 10]) {
		if (value <= step * multiple) return step * multiple;
	}
	return step * 10;
}

/**
 * A column with a rounded cap and square feet.
 *
 * A path rather than `<rect rx>` because `rx` rounds all four corners, and a
 * bar that curves where it meets its own baseline looks like it is floating off
 * the axis.
 */
export function columnPath(x: number, y: number, w: number, h: number): string {
	const r = Math.min(BAR_R, w / 2, h);
	// Rounded to a tenth: band widths divide unevenly, and full float precision
	// puts seventeen meaningless digits into every path in the document.
	const n = (v: number) => v.toFixed(1);
	return (
		`M${n(x)},${n(y + h)} V${n(y + r)} Q${n(x)},${n(y)} ${n(x + r)},${n(y)} ` +
		`H${n(x + w - r)} Q${n(x + w)},${n(y)} ${n(x + w)},${n(y + r)} ` +
		`V${n(y + h)} Z`
	);
}

export type Column = {
	label: string;
	value: number;
	/** Plot-space geometry, already resolved. */
	x: number;
	y: number;
	w: number;
	h: number;
	path: string;
	/** Ticks are thinned so labels never collide. */
	tick: boolean;
	peak: boolean;
};

export type Plot = {
	width: number;
	height: number;
	baseline: number;
	top: number;
	columns: Column[];
	empty: boolean;
};

/** Lay out a single-series column plot. Pure geometry, no colour, no markup. */
export function columns(series: { label: string; value: number }[]): Plot {
	const width = 640;
	const height = 190;
	const padL = 8;
	const padR = 8;
	const padT = 16;
	const padB = 22;
	const plotW = width - padL - padR;
	const plotH = height - padT - padB;
	const empty = series.length === 0 || series.every((d) => d.value === 0);
	const top = niceCeiling(Math.max(0, ...series.map((d) => d.value)));
	const band = series.length ? plotW / series.length : plotW;
	// The 2px separator is surface, not a stroke: neighbours read as distinct
	// because of the gap, never because a border was drawn around them.
	const w = Math.min(BAR_MAX, band - 2);
	// Track the peak VALUE alongside its index. `noUncheckedIndexedAccess` is on
	// in this project, so indexing back into the array to compare would need a
	// guard on every read for a number already in hand.
	let peakAt = 0;
	let peakValue = -Infinity;
	series.forEach((d, i) => {
		if (d.value > peakValue) {
			peakValue = d.value;
			peakAt = i;
		}
	});

	return {
		width,
		height,
		baseline: padT + plotH,
		top,
		empty,
		columns: series.map((d, i) => {
			const h = top ? (d.value / top) * plotH : 0;
			const x = padL + i * band + (band - w) / 2;
			const y = padT + plotH - h;
			return {
				...d,
				x,
				y,
				w,
				h,
				path: columnPath(x, y, w, h),
				tick: i % 2 === 0,
				peak: i === peakAt && d.value > 0
			};
		})
	};
}
