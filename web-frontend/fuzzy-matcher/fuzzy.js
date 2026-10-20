/**
 * fuzzy.js — a tiny, dependency-free fuzzy string matcher and ranker.
 *
 * Given a query and a piece of text, `fuzzyMatch` reports whether the query
 * characters appear in order inside the text (a subsequence match) and scores
 * how "good" that match is. The score rewards matches that are consecutive and
 * that start at a word boundary, so `"fb"` ranks `"Foo Bar"` above `"foobar"`.
 *
 * `search` applies the same logic across a list of items, optionally reading
 * the text to match from one or more object keys, and returns results ranked
 * best-first with the matched character indices (for highlighting).
 *
 * The module is a plain ES module with no runtime dependencies, so it works in
 * the browser, in Node, and in bundlers without a build step.
 */

/** Characters that are considered word separators for boundary bonuses. */
const SEPARATORS = new Set([" ", "-", "_", ".", "/", "\\", ":", ",", ";", "(", ")", "[", "]", "{", "}", "|"]);

/** Tunable weights. Exposed so callers can re-rank for their own domain. */
export const WEIGHTS = {
  base: 1, // every matched character
  consecutive: 5, // extra for a character that directly follows the previous match
  boundary: 8, // extra for a match at the start of a word
  camel: 6, // extra for a match at a lower->upper (camelCase) transition
  firstChar: 4, // extra when the whole query starts at the very beginning
  gapPenalty: 0.5, // penalty per unmatched character inside the matched span
  lengthPenalty: 0.1, // mild preference for shorter text on equal matches
};

function isBoundary(text, index) {
  if (index === 0) return true;
  return SEPARATORS.has(text[index - 1]);
}

function isCamelBoundary(text, index) {
  if (index === 0) return false;
  const prev = text[index - 1];
  const cur = text[index];
  return prev === prev.toLowerCase() && prev !== prev.toUpperCase() && cur === cur.toUpperCase() && cur !== cur.toLowerCase();
}

/**
 * Match `query` against `text` as an in-order subsequence.
 *
 * @param {string} query
 * @param {string} text
 * @param {{caseSensitive?: boolean, weights?: Partial<typeof WEIGHTS>}} [options]
 * @returns {{score: number, indices: number[]} | null} `null` when there is no
 *   match; otherwise the score and the matched character indices in `text`.
 */
export function fuzzyMatch(query, text, options = {}) {
  const caseSensitive = options.caseSensitive === true;
  const w = { ...WEIGHTS, ...(options.weights || {}) };

  if (typeof query !== "string" || typeof text !== "string") {
    throw new TypeError("fuzzyMatch expects string arguments for query and text");
  }

  const q = caseSensitive ? query : query.toLowerCase();
  const t = caseSensitive ? text : text.toLowerCase();

  // An empty query matches everything with a neutral score.
  if (q.length === 0) return { score: 0, indices: [] };
  if (q.length > t.length) return null;

  const indices = [];
  let score = 0;
  let qi = 0;
  let prevIndex = -1;

  for (let ti = 0; ti < t.length && qi < q.length; ti++) {
    if (t[ti] !== q[qi]) continue;

    let charScore = w.base;
    if (prevIndex === ti - 1) charScore += w.consecutive;
    if (isBoundary(t, ti)) charScore += w.boundary;
    else if (isCamelBoundary(text, ti)) charScore += w.camel;
    if (qi === 0 && ti === 0) charScore += w.firstChar;

    score += charScore;
    indices.push(ti);
    prevIndex = ti;
    qi++;
  }

  // Every query character must have been consumed, otherwise there is no match.
  if (qi < q.length) return null;

  const span = indices[indices.length - 1] - indices[0] + 1;
  score -= (span - q.length) * w.gapPenalty;
  score -= (t.length - q.length) * w.lengthPenalty;

  return { score, indices };
}

/**
 * Convenience wrapper that returns only the score (or `-Infinity` on no match),
 * which is handy for `Array.prototype.sort` comparators.
 *
 * @returns {number}
 */
export function fuzzyScore(query, text, options = {}) {
  const m = fuzzyMatch(query, text, options);
  return m ? m.score : -Infinity;
}

/**
 * Read a display string from an item, either the item itself (when it is a
 * string) or one of the requested keys.
 */
function textFor(item, key) {
  if (key == null) return typeof item === "string" ? item : String(item);
  const value = item == null ? undefined : item[key];
  return value == null ? "" : String(value);
}

/**
 * Rank `items` against `query`, best match first.
 *
 * @param {Array} items
 * @param {string} query
 * @param {{
 *   keys?: string[],
 *   limit?: number,
 *   threshold?: number,
 *   caseSensitive?: boolean,
 *   weights?: object,
 * }} [options]
 * @returns {Array<{item: any, score: number, key: string|null, indices: number[]}>}
 */
export function search(items, query, options = {}) {
  if (!Array.isArray(items)) throw new TypeError("search expects an array of items");
  const { keys = null, limit = Infinity, threshold = -Infinity, caseSensitive = false, weights } = options;

  const results = [];
  const matchOpts = { caseSensitive, weights };

  for (let i = 0; i < items.length; i++) {
    const item = items[i];
    const candidateKeys = keys && keys.length ? keys : [null];

    let best = null;
    for (const key of candidateKeys) {
      const text = textFor(item, key);
      const m = fuzzyMatch(query, text, matchOpts);
      if (m && (best === null || m.score > best.score)) {
        best = { score: m.score, key, indices: m.indices };
      }
    }

    if (best && best.score >= threshold) {
      results.push({ item, score: best.score, key: best.key, indices: best.indices, __order: i });
    }
  }

  results.sort((a, b) => (b.score - a.score) || (a.__order - b.__order));

  const trimmed = Number.isFinite(limit) ? results.slice(0, Math.max(0, limit)) : results;
  return trimmed.map(({ item, score, key, indices }) => ({ item, score, key, indices }));
}

/**
 * Split `text` into `{ text, match }` segments using the match indices, so a UI
 * can highlight the matched characters without re-running the matcher.
 *
 * @param {string} text
 * @param {number[]} indices ascending, non-overlapping character indices
 * @returns {Array<{text: string, match: boolean}>}
 */
export function highlightSegments(text, indices) {
  if (!Array.isArray(indices) || indices.length === 0) {
    return text ? [{ text, match: false }] : [];
  }
  const marked = new Set(indices);
  const segments = [];
  let buffer = "";
  let bufferMatch = marked.has(0);

  for (let i = 0; i < text.length; i++) {
    const isMatch = marked.has(i);
    if (isMatch !== bufferMatch && buffer) {
      segments.push({ text: buffer, match: bufferMatch });
      buffer = "";
    }
    bufferMatch = isMatch;
    buffer += text[i];
  }
  if (buffer) segments.push({ text: buffer, match: bufferMatch });
  return segments;
}
