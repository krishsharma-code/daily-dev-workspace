/**
 * toc-spy — generate a table of contents from a document's headings and keep
 * the entry for the section the reader is currently looking at highlighted.
 *
 * No dependencies, no build step. The core functions (slugify, buildOutline,
 * renderOutline, findActiveIndex) are pure and DOM-free, so they can be unit
 * tested in Node; createTocSpy() is the thin DOM controller on top of them.
 *
 * @example
 * import { createTocSpy } from './toc-spy.js';
 * const toc = createTocSpy({ content: document.querySelector('article'), nav: document.querySelector('#toc') });
 * // toc.destroy() when the page/view goes away
 */

const DIACRITIC_MARKS = /[\u0300-\u036f]/g;

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/**
 * Turn heading text into a URL-safe anchor id.
 *
 * Diacritics are folded ("Café" -> "cafe"), "&" reads as "and", apostrophes are
 * dropped rather than turned into separators ("Don't" -> "dont"), and runs of
 * anything else collapse into the separator.
 *
 * @param {string} text
 * @param {{ separator?: string }} [options]
 * @returns {string} never empty — falls back to "section"
 */
export function slugify(text, { separator = '-' } = {}) {
  if (typeof text !== 'string') {
    throw new TypeError('slugify: text must be a string');
  }
  if (typeof separator !== 'string' || separator === '') {
    throw new TypeError('slugify: separator must be a non-empty string');
  }
  const escaped = escapeRegExp(separator);
  const slug = text
    .normalize('NFKD')
    .replace(DIACRITIC_MARKS, '')
    .replace(/&/g, ' and ')
    .replace(/['\u2018\u2019]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, separator)
    .replace(new RegExp(`^(?:${escaped})+|(?:${escaped})+$`, 'g'), '');
  return slug || 'section';
}

function uniqueId(base, used) {
  if (!used.has(base)) {
    used.add(base);
    return base;
  }
  let suffix = 2;
  while (used.has(`${base}-${suffix}`)) suffix += 1;
  const id = `${base}-${suffix}`;
  used.add(id);
  return id;
}

/**
 * Build a nested outline from a flat list of headings.
 *
 * Headings outside [minLevel, maxLevel] and headings with empty text are
 * skipped. A level jump (h2 -> h4) nests under the nearest shallower heading.
 * Duplicate slugs get a numeric suffix ("setup", "setup-2"). An explicit
 * `id` on a heading is kept as-is, and still de-duplicated.
 *
 * @param {Array<{text: string, level: number, id?: string, el?: any}>} headings
 * @param {{ minLevel?: number, maxLevel?: number }} [options]
 * @returns {{ nodes: Array<object>, flat: Array<object> }}
 */
export function buildOutline(headings, { minLevel = 1, maxLevel = 6 } = {}) {
  if (!Array.isArray(headings)) {
    throw new TypeError('buildOutline: headings must be an array');
  }

  const nodes = [];
  const flat = [];
  const stack = [];
  const used = new Set();

  for (const heading of headings) {
    if (!heading) continue;

    const level = Number(heading.level);
    if (!Number.isFinite(level)) {
      throw new TypeError('buildOutline: every heading needs a numeric level');
    }
    if (level < minLevel || level > maxLevel) continue;

    const text = String(heading.text ?? '').replace(/\s+/g, ' ').trim();
    if (!text) continue;

    const explicitId = typeof heading.id === 'string' && heading.id.trim() ? heading.id.trim() : null;
    const id = uniqueId(explicitId ?? slugify(text), used);
    const node = { id, text, level, depth: 0, children: [] };
    if (heading.el) node.el = heading.el;

    while (stack.length > 0 && stack[stack.length - 1].level >= level) stack.pop();
    const parent = stack[stack.length - 1];
    if (parent) parent.children.push(node);
    else nodes.push(node);

    stack.push(node);
    flat.push(node);
  }

  const assignDepth = (list, depth) => {
    for (const node of list) {
      node.depth = depth;
      assignDepth(node.children, depth + 1);
    }
  };
  assignDepth(nodes, 0);

  return { nodes, flat };
}

/**
 * Depth-first list of every node in document order.
 * @param {Array<object>} nodes
 * @returns {Array<object>}
 */
export function flattenOutline(nodes) {
  const out = [];
  const walk = (list) => {
    for (const node of list) {
      out.push(node);
      walk(node.children);
    }
  };
  walk(Array.isArray(nodes) ? nodes : []);
  return out;
}

/**
 * Render an outline as nested list markup. Ids and text are HTML-escaped.
 *
 * @param {Array<object>} nodes
 * @param {{ ordered?: boolean, label?: string }} [options]
 * @returns {string}
 */
export function renderOutline(nodes, { ordered = false, label = null } = {}) {
  const tag = ordered ? 'ol' : 'ul';
  const build = (list) => {
    const items = list
      .map((node) => {
        const children = node.children && node.children.length > 0 ? build(node.children) : '';
        const id = escapeHtml(node.id);
        return (
          `<li class="toc-spy__item toc-spy__item--level-${node.level}">` +
          `<a class="toc-spy__link" href="#${id}" data-toc-id="${id}">${escapeHtml(node.text)}</a>` +
          `${children}</li>`
        );
      })
      .join('');
    return `<${tag} class="toc-spy__list">${items}</${tag}>`;
  };

  const heading = label ? `<p class="toc-spy__label">${escapeHtml(label)}</p>` : '';
  return heading + build(Array.isArray(nodes) ? nodes : []);
}

/**
 * Index of the section that owns the current scroll position: the last entry
 * whose offset is at or above the reading line. Offsets must be ascending
 * (document order). Returns -1 when the reader is above the first heading.
 *
 * @param {number[]} offsets  top of each heading, in document coordinates
 * @param {number} scrollTop  current vertical scroll position
 * @param {{ offset?: number }} [options]  pixels of scroll offset / sticky header height
 * @returns {number}
 */
export function findActiveIndex(offsets, scrollTop, { offset = 0 } = {}) {
  if (!Array.isArray(offsets) || offsets.length === 0) return -1;
  const target = scrollTop + offset;

  let low = 0;
  let high = offsets.length - 1;
  let found = -1;
  while (low <= high) {
    const mid = (low + high) >> 1;
    if (offsets[mid] <= target) {
      found = mid;
      low = mid + 1;
    } else {
      high = mid - 1;
    }
  }
  return found;
}

/**
 * Wire an outline to a scroll position.
 *
 * @param {object} options
 * @param {Element} options.content          element whose headings make up the outline
 * @param {Element} [options.nav]            element to render the outline into
 * @param {string} [options.headingSelector='h2, h3']
 * @param {number} [options.minLevel]        keep headings at or below this level
 * @param {number} [options.maxLevel]        keep headings at or above this level
 * @param {number} [options.offset=0]        sticky-header height, in pixels
 * @param {boolean} [options.smooth=true]    smooth-scroll when a link is clicked
 * @param {boolean} [options.updateHash=false] replace the URL hash on click
 * @param {boolean} [options.ordered=false]  render as an ordered list
 * @param {string|null} [options.label=null] caption above the outline
 * @param {string} [options.activeClass='is-active']
 * @param {(node: object|null, index: number) => void} [options.onActive]
 * @returns {{ refresh: Function, update: Function, destroy: Function, outline: object[], activeIndex: number, nav: Element|null }}
 */
export function createTocSpy(options = {}) {
  const {
    content,
    nav = null,
    headingSelector = 'h2, h3',
    minLevel,
    maxLevel,
    offset = 0,
    smooth = true,
    updateHash = false,
    ordered = false,
    label = null,
    activeClass = 'is-active',
    onActive = null,
  } = options;

  if (!content || typeof content.querySelectorAll !== 'function') {
    throw new Error('createTocSpy: content element is required');
  }
  const doc = content.ownerDocument || (typeof document !== 'undefined' ? document : null);
  if (!doc) throw new Error('createTocSpy: no document available');
  const win = doc.defaultView;
  if (!win || typeof win.addEventListener !== 'function') {
    throw new Error('createTocSpy: the content element must be attached to a window');
  }

  const raf =
    typeof win.requestAnimationFrame === 'function'
      ? (fn) => win.requestAnimationFrame(fn)
      : (fn) => win.setTimeout(fn, 16);
  const caf =
    typeof win.cancelAnimationFrame === 'function'
      ? (id) => win.cancelAnimationFrame(id)
      : (id) => win.clearTimeout(id);

  let nodes = [];
  let flat = [];
  let offsets = [];
  let activeIndex = -1;
  let destroyed = false;
  let frame = 0;

  const behavior = () => (smooth ? 'smooth' : 'auto');

  function measure() {
    offsets = flat.map((node) => {
      if (!node.el) return Number.POSITIVE_INFINITY;
      return node.el.getBoundingClientRect().top + win.scrollY;
    });
  }

  function paint(index) {
    if (!nav) return;
    const activeId = index >= 0 && flat[index] ? flat[index].id : null;
    const links = nav.querySelectorAll('[data-toc-id]');
    for (const link of links) {
      const isActive = activeId !== null && link.getAttribute('data-toc-id') === activeId;
      link.classList.toggle(activeClass, isActive);
      const item = typeof link.closest === 'function' ? link.closest('li') : null;
      if (item) item.classList.toggle(activeClass, isActive);
      if (isActive) link.setAttribute('aria-current', 'location');
      else link.removeAttribute('aria-current');
    }
  }

  function setActive(index) {
    const next = Number.isInteger(index) ? index : -1;
    if (next === activeIndex) return;
    activeIndex = next;
    paint(next);
    if (typeof onActive === 'function') onActive(next >= 0 ? flat[next] : null, next);
  }

  function update() {
    if (destroyed) return;
    const y = typeof win.scrollY === 'number' ? win.scrollY : doc.documentElement.scrollTop || 0;
    const docHeight = doc.documentElement ? doc.documentElement.scrollHeight : 0;
    // Only treat a real, scrollable document as "at the bottom".
    const atBottom = docHeight > win.innerHeight && y + win.innerHeight >= docHeight - 2;
    if (atBottom && flat.length > 0) {
      setActive(flat.length - 1);
      return;
    }
    setActive(findActiveIndex(offsets, y, { offset }));
  }

  function refresh() {
    if (destroyed) return;
    const headings = Array.from(content.querySelectorAll(headingSelector)).map((el) => ({
      text: el.textContent || '',
      level: Number(String(el.tagName || '').slice(1)) || 2,
      id: el.id || null,
      el,
    }));

    const built = buildOutline(headings, {
      minLevel: Number.isFinite(minLevel) ? minLevel : 1,
      maxLevel: Number.isFinite(maxLevel) ? maxLevel : 6,
    });
    nodes = built.nodes;
    flat = built.flat;

    for (const node of flat) {
      if (node.el && !node.el.id) node.el.id = node.id;
    }

    if (nav) nav.innerHTML = renderOutline(nodes, { ordered, label });
    measure();
    activeIndex = -1;
    update();
  }

  function onScroll() {
    if (frame || destroyed) return;
    frame = raf(() => {
      frame = 0;
      update();
    });
  }

  function onResize() {
    if (destroyed) return;
    measure();
    update();
  }

  function onClick(event) {
    if (!nav) return;
    const target = event.target;
    const link = target && typeof target.closest === 'function' ? target.closest('[data-toc-id]') : null;
    if (!link || !nav.contains(link)) return;

    const id = link.getAttribute('data-toc-id');
    const node = flat.find((entry) => entry.id === id);
    if (!node || !node.el) return;

    event.preventDefault();
    const top = node.el.getBoundingClientRect().top + win.scrollY - offset;
    if (typeof win.scrollTo === 'function') win.scrollTo({ top, behavior: behavior() });
    else win.scrollY = top;

    if (updateHash && win.history && typeof win.history.replaceState === 'function') {
      try {
        win.history.replaceState(null, '', `#${id}`);
      } catch {
        // Some embedded webviews refuse history writes; the scroll already happened.
      }
    }
    setActive(flat.indexOf(node));
  }

  function destroy() {
    if (destroyed) return;
    destroyed = true;
    if (frame) caf(frame);
    frame = 0;
    win.removeEventListener('scroll', onScroll);
    win.removeEventListener('resize', onResize);
    if (nav) nav.removeEventListener('click', onClick);
    if (observer) observer.disconnect();
  }

  let observer = null;
  if (typeof win.ResizeObserver === 'function') {
    observer = new win.ResizeObserver(onResize);
    observer.observe(content);
  }

  win.addEventListener('scroll', onScroll, { passive: true });
  win.addEventListener('resize', onResize);
  if (nav) nav.addEventListener('click', onClick);

  refresh();

  return {
    refresh,
    update,
    destroy,
    get outline() {
      return nodes;
    },
    get activeIndex() {
      return activeIndex;
    },
    nav,
  };
}

export default createTocSpy;
