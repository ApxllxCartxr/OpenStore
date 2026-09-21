/**
 * Placeholder tiles for a shop with no photography.
 *
 * They say what they are. A grey box implies a photograph that failed to
 * load; a tile that names the item and reads "placeholder" is honest about a
 * shop that has not been photographed yet, and the catalogue, the feed and
 * the agent's result cards all have something to render.
 *
 * SVG, written by hand: a raster pipeline would be a build dependency for
 * eleven flat rectangles.
 */
import { mkdirSync, writeFileSync } from 'node:fs';
import { DOGEARED } from './profiles/dogeared.ts';
import type { Profile } from './seed-core.ts';

const OUT = new URL('../static/media/', import.meta.url);

/** Cloth colours, picked per item so a shelf of them does not read as one
 *  product photographed twelve times. Muted and low-contrast against the
 *  paper ground; the title carries the tile, not the colour. */
const CLOTH = ['#3f4a55', '#5a4a42', '#4a5548', '#55454f', '#46505c', '#5c5140'];

function escape(text: string): string {
	return text.replace(/[<>&]/g, (c) => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;' })[c] ?? c);
}

/** Break a title across at most four lines of roughly even length, so a long
 *  one wraps instead of running off the tile. */
function wrap(title: string, perLine = 16): string[] {
	const lines: string[] = [];
	let line = '';
	for (const word of title.split(/\s+/)) {
		if (line && (line + ' ' + word).length > perLine) {
			lines.push(line);
			line = word;
		} else {
			line = line ? `${line} ${word}` : word;
		}
	}
	if (line) lines.push(line);
	return lines.slice(0, 4);
}

export function tile(name: string, shop: string, cloth: string): string {
	const [title = '', detail = ''] = name.split(' — ');
	const lines = wrap(title);
	const top = 470 - (lines.length - 1) * 33;
	const rows = lines
		.map((line, i) => `<tspan x="500" y="${top + i * 66}">${escape(line)}</tspan>`)
		.join('');
	return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 1250" width="1000" height="1250" role="img" aria-label="${escape(name)}">
  <rect width="1000" height="1250" fill="#efe9e0"/>
  <rect x="120" y="150" width="760" height="950" rx="6" fill="${cloth}"/>
  <rect x="150" y="180" width="700" height="890" rx="3" fill="none" stroke="#efe9e0" stroke-opacity="0.35" stroke-width="2"/>
  <text x="500" text-anchor="middle" font-family="Georgia, 'Times New Roman', serif" font-size="56" fill="#f4efe6">${rows}</text>
  ${detail ? `<text x="500" y="${top + lines.length * 66 + 20}" text-anchor="middle" font-family="Georgia, serif" font-size="34" fill="#f4efe6" fill-opacity="0.7">${escape(detail)}</text>` : ''}
  <text x="500" y="1010" text-anchor="middle" font-family="ui-monospace, monospace" font-size="26" fill="#f4efe6" fill-opacity="0.55">${escape(shop)}</text>
  <text x="500" y="1160" text-anchor="middle" font-family="ui-monospace, monospace" font-size="24" fill="#8a8078">placeholder — not a photograph</text>
</svg>
`;
}

export function writeTiles(profile: Profile): string[] {
	mkdirSync(OUT, { recursive: true });
	const written: string[] = [];
	profile.items.forEach((item, n) => {
		for (const path of profile.media[item.sku] ?? []) {
			if (!path.endsWith('.svg')) continue;
			const file = new URL(`.${path.replace('/media', '')}`, OUT);
			writeFileSync(file, tile(item.name, profile.shop, CLOTH[n % CLOTH.length]!));
			written.push(path);
		}
	});
	return written;
}

if (import.meta.url === `file://${process.argv[1]}`) {
	const written = writeTiles(DOGEARED);
	console.log(`Wrote ${written.length} placeholder tiles to static/media/.`);
}
