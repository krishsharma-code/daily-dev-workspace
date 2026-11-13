import test from 'node:test';
import assert from 'node:assert/strict';

import {
  slugify,
  buildOutline,
  flattenOutline,
  renderOutline,
  findActiveIndex,
} from './toc-spy.js';

const h = (text, level, extra = {}) => ({ text, level, ...extra });

test('slugify folds diacritics and punctuation', () => {
  assert.equal(slugify('Getting Started'), 'getting-started');
  assert.equal(slugify('  Café  crème  '), 'cafe-creme');
  assert.equal(slugify('Node.js & Deno'), 'node-js-and-deno');
  assert.equal(slugify("Don't Panic"), 'dont-panic');
  assert.equal(slugify('FAQ’s'), 'faqs');
  assert.equal(slugify('Hello, world!'), 'hello-world');
  assert.equal(slugify('——'), 'section');
  assert.equal(slugify(''), 'section');
});

test('slugify supports a custom separator', () => {
  assert.equal(slugify('API Reference v2', { separator: '_' }), 'api_reference_v2');
  assert.equal(slugify('a.b+c', { separator: '.' }), 'a.b.c');
});

test('slugify rejects bad input', () => {
  assert.throws(() => slugify(42), TypeError);
  assert.throws(() => slugify('x', { separator: '' }), TypeError);
});

test('buildOutline nests by heading level', () => {
  const { nodes, flat } = buildOutline([
    h('Intro', 2),
    h('Install', 3),
    h('Configure', 3),
    h('Advanced', 2),
    h('Tuning', 3),
  ]);

  assert.equal(nodes.length, 2);
  assert.equal(nodes[0].id, 'intro');
  assert.deepEqual(nodes[0].children.map((n) => n.id), ['install', 'configure']);
  assert.deepEqual(nodes[1].children.map((n) => n.id), ['tuning']);
  assert.deepEqual(flat.map((n) => n.id), ['intro', 'install', 'configure', 'advanced', 'tuning']);
  assert.deepEqual(flat.map((n) => n.depth), [0, 1, 1, 0, 1]);
});

test('buildOutline nests a level jump under the nearest shallower heading', () => {
  const { nodes } = buildOutline([h('Top', 2), h('Deep', 4), h('Sibling', 3)]);
  assert.equal(nodes.length, 1);
  // h3 after h4 is a sibling of the h4, both still under the h2.
  assert.deepEqual(nodes[0].children.map((n) => n.id), ['deep', 'sibling']);
  assert.deepEqual(nodes[0].children[0].children, []);
});

test('buildOutline filters by level and skips blank headings', () => {
  const { flat } = buildOutline(
    [h('Title', 1), h('Section', 2), h('  ', 2), h('Sub', 3), h('Note', 5)],
    { minLevel: 2, maxLevel: 3 },
  );
  assert.deepEqual(flat.map((n) => n.text), ['Section', 'Sub']);
});

test('buildOutline de-duplicates slugs and keeps explicit ids', () => {
  const { flat } = buildOutline([
    h('Setup', 2),
    h('Setup', 2),
    h('Setup', 2),
    h('Custom', 2, { id: 'my-anchor' }),
    h('Custom', 2, { id: 'my-anchor' }),
  ]);
  assert.deepEqual(flat.map((n) => n.id), ['setup', 'setup-2', 'setup-3', 'my-anchor', 'my-anchor-2']);
});

test('buildOutline collapses whitespace and carries the source element', () => {
  const el = { id: '' };
  const { flat } = buildOutline([h('  Many \n  spaces\t here ', 2, { el })]);
  assert.equal(flat[0].text, 'Many spaces here');
  assert.equal(flat[0].id, 'many-spaces-here');
  assert.equal(flat[0].el, el);
});

test('buildOutline rejects malformed input', () => {
  assert.throws(() => buildOutline('nope'), TypeError);
  assert.throws(() => buildOutline([{ text: 'x', level: 'two' }]), TypeError);
});

test('flattenOutline returns document order depth-first', () => {
  const { nodes } = buildOutline([h('A', 2), h('A1', 3), h('A1a', 4), h('B', 2)]);
  assert.deepEqual(flattenOutline(nodes).map((n) => n.id), ['a', 'a1', 'a1a', 'b']);
  assert.deepEqual(flattenOutline([]), []);
});

test('renderOutline produces nested, escaped markup', () => {
  const { nodes } = buildOutline([h('Tom & Jerry', 2), h('Sub <script>', 3)]);
  const html = renderOutline(nodes, { label: 'On this page' });

  assert.match(html, /<p class="toc-spy__label">On this page<\/p>/);
  assert.match(html, /<ul class="toc-spy__list">/);
  assert.match(html, /href="#tom-and-jerry"/);
  assert.match(html, /data-toc-id="sub-script"/);
  assert.match(html, /Tom &amp; Jerry/);
  assert.match(html, /Sub &lt;script&gt;/);
  assert.doesNotMatch(html, /<script>/);
  assert.equal((html.match(/<ul/g) || []).length, 2);
});

test('renderOutline can use an ordered list and omit the label', () => {
  const { nodes } = buildOutline([h('One', 2)]);
  const html = renderOutline(nodes, { ordered: true });
  assert.match(html, /^<ol class="toc-spy__list">/);
  assert.doesNotMatch(html, /toc-spy__label/);
});

test('findActiveIndex picks the last heading at or above the reading line', () => {
  const offsets = [0, 400, 900, 1500];
  assert.equal(findActiveIndex(offsets, 0), 0);
  assert.equal(findActiveIndex(offsets, 399), 0);
  assert.equal(findActiveIndex(offsets, 400), 1);
  assert.equal(findActiveIndex(offsets, 1200), 2);
  assert.equal(findActiveIndex(offsets, 99999), 3);
});

test('findActiveIndex returns -1 above the first heading', () => {
  assert.equal(findActiveIndex([200, 800], 0), -1);
  assert.equal(findActiveIndex([200, 800], 150), -1);
  assert.equal(findActiveIndex([200, 800], 0, { offset: 200 }), 0);
});

test('findActiveIndex honours a sticky-header offset', () => {
  const offsets = [100, 500];
  assert.equal(findActiveIndex(offsets, 60), -1);
  assert.equal(findActiveIndex(offsets, 60, { offset: 64 }), 0);
  assert.equal(findActiveIndex(offsets, 430, { offset: 64 }), 0);
  assert.equal(findActiveIndex(offsets, 450, { offset: 64 }), 1);
});

test('findActiveIndex handles empty and single-entry lists', () => {
  assert.equal(findActiveIndex([], 10), -1);
  assert.equal(findActiveIndex([0], 10), 0);
  assert.equal(findActiveIndex([500], 10), -1);
});
