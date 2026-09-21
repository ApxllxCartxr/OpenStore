/**
 * The renderer is fed model output, so the first thing tested is that no model
 * can put HTML on this page. The rest is the subset a shop assistant writes.
 */
import { describe, expect, it } from 'vitest';
import { renderMarkdown } from '../src/lib/markdown.ts';

describe('nothing the model writes becomes HTML', () => {
	it('escapes tags, attributes and entities', () => {
		const out = renderMarkdown('<img src=x onerror="alert(1)"> & <script>alert(2)</script>');
		// The tag never opens, so the attribute beside it is only ever text.
		expect(out).not.toMatch(/<img|<script/);
		expect(out).toContain('onerror=&quot;');
		expect(out).toContain('&lt;img');
		expect(out).toContain('&amp;');
	});

	it('escapes inside code spans and fences too', () => {
		expect(renderMarkdown('`<b>x</b>`')).toContain('<code>&lt;b&gt;x&lt;/b&gt;</code>');
		expect(renderMarkdown('```\n<b>x</b>\n```')).toContain('<pre><code>&lt;b&gt;x&lt;/b&gt;</code></pre>');
	});

	it('renders a link as its text and never as an anchor', () => {
		// The approve link comes from a signed tool result. An anchor the model
		// wrote would look exactly like it.
		const out = renderMarkdown('Approve it [here](http://not-the-shop.test/pay).');
		expect(out).not.toContain('<a');
		expect(out).not.toContain('not-the-shop.test');
		expect(out).toContain('Approve it here.');
	});
});

describe('the marks a shop assistant actually writes', () => {
	it('renders bold, italic and code', () => {
		expect(renderMarkdown('**Tote** is *lovely* and costs `SD-TOTE-RED-L`')).toBe(
			'<p><strong>Tote</strong> is <em>lovely</em> and costs <code>SD-TOTE-RED-L</code></p>'
		);
	});

	it('leaves marks inside an identifier alone', () => {
		expect(renderMarkdown('SD_TOTE_RED_L')).toBe('<p>SD_TOTE_RED_L</p>');
	});

	it('renders a bullet list, with or without a blank line above it', () => {
		const out = renderMarkdown('Here are a few ideas:\n- **Tote** – practical\n- Phone charm');
		expect(out).toBe(
			'<p>Here are a few ideas:</p><ul><li><strong>Tote</strong> – practical</li><li>Phone charm</li></ul>'
		);
	});

	it('renders a numbered list', () => {
		expect(renderMarkdown('1. Pick a size\n2. Give an address')).toBe(
			'<ol><li>Pick a size</li><li>Give an address</li></ol>'
		);
	});

	it('keeps a paragraph’s own line breaks and separates paragraphs', () => {
		expect(renderMarkdown('One\nTwo\n\nThree')).toBe('<p>One<br />Two</p><p>Three</p>');
	});

	it('renders a heading as a strong line, not a document heading', () => {
		const out = renderMarkdown('## Gift ideas');
		expect(out).toBe('<p class="md-lead">Gift ideas</p>');
		expect(out).not.toContain('<h2');
	});

	it('renders an empty message as nothing at all', () => {
		expect(renderMarkdown('   \n  ')).toBe('');
	});
});
