/**
 * snippet-kit — safe, framework-free search-result snippets.
 *
 * A search box that shows raw matches is easy; one that shows *good* snippets
 * is not. This module covers the parts that are easy to get wrong:
 *
 *   - turning a query into terms, honouring "quoted phrases";
 *   - finding every match, case-insensitively, without breaking on Unicode
 *     (Devanagari, Tamil, emoji) or on indices shifted by case folding;
 *   - merging overlapping and touching ranges so a word is highlighted once;
 *   - cutting a window around the match with an ellipsis at each trimmed end,
 *     never splitting a word, a surrogate pair or a combining mark;
 *   - escaping HTML, so a snippet of user content can never inject markup.
 *
 * Everything is a pure function over strings: no DOM, no dependencies, no
 * build step. Render `annotate()` output with your own framework, or take the
 * ready-made `highlightHtml()` / `snippetHtml()` strings.
 *
 * @module snippet-kit
 */

const WORD_CHAR = /[\p{L}\p{N}_]/u;
const COMBINING_MARK = /\p{M}/u;
const HTML_ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

export const DEFAULT_ELLIPSIS = "…";

/* -------------------------------------------------------------------------- */
/* small helpers                                                              */
/* -------------------------------------------------------------------------- */

function assertString(value, name) {
  if (typeof value !== "string") {
    throw new TypeError(`snippet-kit: ${name} must be a string, received ${typeof value}`);
  }
}

/**
 * Lowercase without changing string length, so match offsets stay valid.
 *
 * `String.prototype.toLowerCase` can expand a character (for example "İ"
 * becomes two code units). Such characters are left as they are: a match on
 * them then needs the exact case, which is better than every later offset in
 * the text being wrong.
 */
function fold(text) {
  let out = "";
  for (const ch of text) {
    const lower = ch.toLowerCase();
    out += lower.length === ch.length ? lower : ch;
  }
  return out;
}

/** The code point ending at `index`, surrogate-pair aware. */
function charEndingAt(text, index) {
  if (index <= 0) return "";
  let start = index - 1;
  const unit = text.charCodeAt(start);
  if (unit >= 0xdc00 && unit <= 0xdfff && start > 0) start -= 1;
  return text.slice(start, index);
}

/** The code point starting at `index`. */
function charStartingAt(text, index) {
  if (index >= text.length) return "";
  return String.fromCodePoint(text.codePointAt(index));
}

function isWordChar(ch) {
  return ch !== "" && WORD_CHAR.test(ch);
}

function isLowSurrogate(unit) {
  return unit >= 0xdc00 && unit <= 0xdfff;
}

function isHighSurrogate(unit) {
  return unit >= 0xd800 && unit <= 0xdbff;
}

/* -------------------------------------------------------------------------- */
/* query parsing                                                              */
/* -------------------------------------------------------------------------- */

function dedupeTerms(terms) {
  const seen = new Set();
  const kept = [];
  for (const term of terms) {
    const key = fold(term.text);
    if (!key || seen.has(key)) continue;
    seen.add(key);
    kept.push(term);
  }
  // Longest first: a phrase consumes what its words would otherwise match, and
  // merging keeps the result correct either way.
  return kept.sort((a, b) => b.text.length - a.text.length || (a.text < b.text ? -1 : 1));
}

function toTermList(terms) {
  if (terms == null) return [];
  if (typeof terms === "string") return parseQuery(terms);
  if (Array.isArray(terms)) {
    const list = terms.map((term) => {
      if (typeof term === "string") {
        const text = term.trim();
        return { text, phrase: /\s/.test(text) };
      }
      if (term && typeof term === "object" && typeof term.text === "string") {
        const text = term.text.trim();
        return { text, phrase: term.phrase === undefined ? /\s/.test(text) : Boolean(term.phrase) };
      }
      throw new TypeError("snippet-kit: terms must be strings or { text, phrase } objects");
    });
    return dedupeTerms(list.filter((term) => term.text));
  }
  throw new TypeError("snippet-kit: terms must be a query string or an array of terms");
}

/**
 * Split a query into searchable terms.
 *
 * `"gst invoice" refund` becomes a phrase term `gst invoice` and a word term
 * `refund`. Duplicates (case-insensitive) are dropped and longer terms come
 * first.
 *
 * @param {string} query
 * @returns {{ text: string, phrase: boolean }[]}
 */
export function parseQuery(query) {
  assertString(query, "query");
  const terms = [];
  const pattern = /"([^"]*)"|(\S+)/g;
  let match;
  while ((match = pattern.exec(query)) !== null) {
    const phrase = match[1] !== undefined;
    const text = (phrase ? match[1] : match[2]).trim();
    if (text) terms.push({ text, phrase });
  }
  return dedupeTerms(terms);
}

/* -------------------------------------------------------------------------- */
/* matching                                                                   */
/* -------------------------------------------------------------------------- */

function isWholeWord(text, start, end) {
  return !isWordChar(charEndingAt(text, start)) && !isWordChar(charStartingAt(text, end));
}

function mergeRanges(ranges) {
  if (ranges.length < 2) return ranges;
  const sorted = ranges.slice().sort((a, b) => a.start - b.start || b.end - a.end);
  const merged = [sorted[0]];
  for (let i = 1; i < sorted.length; i += 1) {
    const last = merged[merged.length - 1];
    const next = sorted[i];
    if (next.start <= last.end) {
      if (next.end > last.end) last.end = next.end;
    } else {
      merged.push(next);
    }
  }
  return merged;
}

/**
 * Every match of `terms` inside `text`, as merged, sorted, non-overlapping
 * ranges of `{ start, end }` (end exclusive, like `String.prototype.slice`).
 *
 * Overlapping and touching matches are merged, so "gst invoice" marks one span
 * rather than two abutting ones.
 *
 * @param {string} text
 * @param {string | (string | {text: string, phrase?: boolean})[]} terms
 * @param {{ caseSensitive?: boolean, wholeWord?: boolean }} [options]
 * @returns {{ start: number, end: number }[]}
 */
export function findMatches(text, terms, options = {}) {
  assertString(text, "text");
  const caseSensitive = options.caseSensitive === true;
  const wholeWord = options.wholeWord === true;
  const list = toTermList(terms);
  if (!text || list.length === 0) return [];

  const haystack = caseSensitive ? text : fold(text);
  const found = [];

  for (const term of list) {
    const needle = caseSensitive ? term.text : fold(term.text);
    if (!needle) continue;
    let from = 0;
    for (;;) {
      const at = haystack.indexOf(needle, from);
      if (at === -1) break;
      const end = at + needle.length;
      if (!wholeWord || isWholeWord(text, at, end)) found.push({ start: at, end });
      from = at + 1; // keep scanning, so overlapping occurrences are seen too
    }
  }

  return mergeRanges(found);
}

/**
 * How many merged match ranges `terms` produces in `text`.
 *
 * @returns {number}
 */
export function countMatches(text, terms, options = {}) {
  return findMatches(text, terms, options).length;
}

/* -------------------------------------------------------------------------- */
/* rendering                                                                  */
/* -------------------------------------------------------------------------- */

/**
 * Escape `& < > " '` so a string can be placed in HTML text or an attribute.
 *
 * @param {unknown} value
 * @returns {string}
 */
export function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (ch) => HTML_ESCAPES[ch]);
}

/**
 * Split `text` into alternating plain and matched segments. Handy when the
 * caller renders with a framework instead of an HTML string.
 *
 * @param {string} text
 * @param {string | (string | {text: string, phrase?: boolean})[]} terms
 * @param {{ caseSensitive?: boolean, wholeWord?: boolean }} [options]
 * @returns {{ text: string, match: boolean }[]}
 */
export function annotate(text, terms, options = {}) {
  assertString(text, "text");
  return segmentsFor(text, findMatches(text, terms, options));
}

function segmentsFor(text, ranges) {
  const segments = [];
  let cursor = 0;
  for (const range of ranges) {
    if (range.start > cursor) segments.push({ text: text.slice(cursor, range.start), match: false });
    segments.push({ text: text.slice(range.start, range.end), match: true });
    cursor = range.end;
  }
  if (cursor < text.length) segments.push({ text: text.slice(cursor), match: false });
  return segments;
}

function renderRanges(text, ranges, options = {}) {
  const tag = options.tag === undefined ? "mark" : String(options.tag);
  const className = options.className ? String(options.className) : "";
  const open = className ? `<${tag} class="${escapeHtml(className)}">` : `<${tag}>`;
  const close = `</${tag}>`;
  return segmentsFor(text, ranges)
    .map((segment) => (segment.match ? open + escapeHtml(segment.text) + close : escapeHtml(segment.text)))
    .join("");
}

/**
 * `text` as escaped HTML with every match wrapped in a tag (default `<mark>`).
 *
 * @param {string} text
 * @param {string | (string | {text: string, phrase?: boolean})[]} terms
 * @param {{ caseSensitive?: boolean, wholeWord?: boolean, tag?: string, className?: string }} [options]
 * @returns {string}
 */
export function highlightHtml(text, terms, options = {}) {
  assertString(text, "text");
  const { tag, className, ...matchOptions } = options;
  return renderRanges(text, findMatches(text, terms, matchOptions), { tag, className });
}

/* -------------------------------------------------------------------------- */
/* snippets                                                                   */
/* -------------------------------------------------------------------------- */

function snapStart(text, start, floor) {
  if (start <= 0) return 0;
  const limit = Math.min(text.length, start + 24);
  for (let i = start; i < limit; i += 1) {
    if (/\s/.test(text[i])) {
      const candidate = i + 1;
      return candidate <= floor ? candidate : start;
    }
  }
  return start;
}

function snapEnd(text, end, ceiling) {
  if (end >= text.length) return text.length;
  const limit = Math.max(0, end - 24);
  for (let i = end - 1; i >= limit; i -= 1) {
    if (/\s/.test(text[i])) {
      return i >= ceiling ? i : end;
    }
  }
  return end;
}

/** Never begin a window on a low surrogate or an orphaned combining mark. */
function safeStart(text, index) {
  let i = Math.max(0, Math.min(index, text.length));
  if (i > 0 && i < text.length && isLowSurrogate(text.charCodeAt(i))) i -= 1;
  if (i > 0 && i < text.length && COMBINING_MARK.test(charStartingAt(text, i))) i -= 1;
  return i;
}

/** Never end a window between a high surrogate and its pair, or before a mark. */
function safeEnd(text, index) {
  let i = Math.max(0, Math.min(index, text.length));
  if (i > 0 && i < text.length && isHighSurrogate(text.charCodeAt(i - 1))) i -= 1;
  if (i > 0 && i < text.length && COMBINING_MARK.test(charStartingAt(text, i))) i += 1;
  return i;
}

function computeWindow(text, ranges, size) {
  const anchor = ranges[0];

  // A single match longer than the budget is shown whole rather than truncated.
  if (anchor.end - anchor.start >= size) return { start: anchor.start, end: anchor.end };

  const lead = Math.floor(size * 0.35);
  let start = Math.max(0, anchor.start - lead);
  let end = start + size;
  if (end > text.length) {
    end = text.length;
    start = Math.max(0, end - size);
  }

  // Do not cut through a match that straddles an edge: widen to include it.
  for (const range of ranges) {
    if (range.start < start && range.end > start) start = range.start;
    if (range.start < end && range.end > end) end = range.end;
  }

  // Widen for a straddling match, then trim the roomier side back to budget.
  while (end - start > size) {
    const roomLeft = anchor.start - start;
    const roomRight = end - anchor.end;
    if (roomRight >= roomLeft) {
      end = Math.max(anchor.end, end - (end - start - size));
    } else {
      start = Math.min(anchor.start, start + (end - start - size));
    }
    if (anchor.start - start <= 0 && end - anchor.end <= 0) break;
  }

  start = safeStart(text, snapStart(text, start, anchor.start));
  end = safeEnd(text, snapEnd(text, end, anchor.end));
  return { start, end };
}

/**
 * A short, match-centred window of `text` for a search result.
 *
 * The window opens around the first match, ends on word boundaries where it
 * can, and reports whether each end was trimmed so the caller can show an
 * ellipsis. Ranges are re-based onto the returned `text`.
 *
 * @param {string} text
 * @param {string | (string | {text: string, phrase?: boolean})[]} terms
 * @param {{
 *   length?: number,
 *   ellipsis?: string,
 *   caseSensitive?: boolean,
 *   wholeWord?: boolean,
 *   normalizeWhitespace?: boolean,
 * }} [options]
 * @returns {{
 *   text: string,
 *   ranges: { start: number, end: number }[],
 *   leadingEllipsis: boolean,
 *   trailingEllipsis: boolean,
 *   matched: number,
 * }}
 */
export function snippet(text, terms, options = {}) {
  assertString(text, "text");
  const length = Math.max(1, Math.floor(options.length === undefined ? 160 : Number(options.length) || 160));
  const normalizeWhitespace = options.normalizeWhitespace !== false;
  const matchOptions = { caseSensitive: options.caseSensitive === true, wholeWord: options.wholeWord === true };

  const source = normalizeWhitespace ? text.replace(/\s+/g, " ") : text;
  const ranges = findMatches(source, terms, matchOptions);

  if (source.length <= length) {
    return { text: source, ranges, leadingEllipsis: false, trailingEllipsis: false, matched: ranges.length };
  }

  if (ranges.length === 0) {
    const end = safeEnd(source, snapEnd(source, length, 0));
    return {
      text: source.slice(0, end),
      ranges: [],
      leadingEllipsis: false,
      trailingEllipsis: end < source.length,
      matched: 0,
    };
  }

  const { start, end } = computeWindow(source, ranges, length);
  const windowText = source.slice(start, end);
  const local = [];
  for (const range of ranges) {
    if (range.start >= start && range.end <= end) {
      local.push({ start: range.start - start, end: range.end - start });
    }
  }

  return {
    text: windowText,
    ranges: local,
    leadingEllipsis: start > 0,
    trailingEllipsis: end < source.length,
    matched: local.length,
  };
}

/**
 * `snippet()` rendered straight to escaped HTML, ellipses included.
 *
 * @param {string} text
 * @param {string | (string | {text: string, phrase?: boolean})[]} terms
 * @param {Parameters<typeof snippet>[2] & { tag?: string, className?: string }} [options]
 * @returns {string}
 */
export function snippetHtml(text, terms, options = {}) {
  const { tag, className, ...snippetOptions } = options;
  const ellipsis = snippetOptions.ellipsis === undefined ? DEFAULT_ELLIPSIS : String(snippetOptions.ellipsis);
  const result = snippet(text, terms, snippetOptions);
  const body = renderRanges(result.text, result.ranges, { tag, className });
  const lead = result.leadingEllipsis ? escapeHtml(ellipsis) : "";
  const trail = result.trailingEllipsis ? escapeHtml(ellipsis) : "";
  return lead + body + trail;
}
