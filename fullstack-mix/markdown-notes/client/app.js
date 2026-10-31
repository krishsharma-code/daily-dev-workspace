/* Markdown Notes client.
 *
 * The server owns Markdown rendering (and escaping), so the preview is the
 * server's HTML dropped into the pane. Everything else here is UI state:
 * the note list, the editor, and debounced live preview.
 */
'use strict';

const els = {
  list: document.getElementById('note-list'),
  search: document.getElementById('search'),
  title: document.getElementById('title'),
  body: document.getElementById('body'),
  preview: document.getElementById('preview'),
  outline: document.getElementById('outline'),
  meta: document.getElementById('meta'),
  status: document.getElementById('status'),
  save: document.getElementById('save-note'),
  remove: document.getElementById('delete-note'),
  create: document.getElementById('new-note'),
};

const state = {
  notes: [],
  currentId: null,
  dirty: false,
  saving: false,
  previewToken: 0,
};

const PREVIEW_DELAY = 180;
let previewTimer = null;

async function api(method, path, payload) {
  const options = { method, headers: {} };
  if (payload !== undefined) {
    options.headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(payload);
  }
  const response = await fetch(path, options);
  if (response.status === 204) return null;
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}

function setStatus(text) {
  els.status.textContent = text;
  if (text) {
    clearTimeout(setStatus.timer);
    setStatus.timer = setTimeout(() => { els.status.textContent = ''; }, 2500);
  }
}

function formatDate(iso) {
  if (!iso) return '';
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleDateString();
}

function markDirty(dirty) {
  state.dirty = dirty;
  els.save.disabled = state.saving || !dirty;
  els.meta.textContent = dirty ? 'Unsaved changes' : (state.currentId ? 'Saved' : '');
}

function renderList() {
  els.list.textContent = '';
  if (!state.notes.length) {
    const empty = document.createElement('li');
    empty.className = 'empty';
    empty.textContent = els.search.value ? 'No matches.' : 'No notes yet.';
    els.list.append(empty);
    return;
  }
  for (const note of state.notes) {
    const item = document.createElement('li');
    item.dataset.id = String(note.id);
    if (note.id === state.currentId) item.classList.add('active');

    const title = document.createElement('span');
    title.className = 'note-title';
    title.textContent = note.title;

    const date = document.createElement('span');
    date.className = 'note-date';
    date.textContent = formatDate(note.updated_at);

    item.append(title, date);
    els.list.append(item);
  }
}

function renderOutline(headings) {
  els.outline.textContent = '';
  const visible = (headings || []).filter((h) => h.level <= 3);
  els.outline.hidden = visible.length === 0;
  for (const heading of visible) {
    const link = document.createElement('a');
    link.href = `#${heading.id}`;
    link.textContent = heading.text || heading.id;
    link.addEventListener('click', (event) => {
      event.preventDefault();
      const target = document.getElementById(heading.id);
      if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
    els.outline.append(link);
  }
}

async function refreshPreview() {
  const text = els.body.value;
  const token = ++state.previewToken;
  if (!text.trim()) {
    els.preview.textContent = '';
    const empty = document.createElement('p');
    empty.className = 'empty';
    empty.textContent = 'Nothing to preview yet.';
    els.preview.append(empty);
    renderOutline([]);
    return;
  }
  try {
    const data = await api('POST', '/api/preview', { text });
    if (token !== state.previewToken) return; // a newer keystroke won
    els.preview.innerHTML = data.html; // server output is escaped by construction
    renderOutline(data.headings);
  } catch (error) {
    if (token === state.previewToken) setStatus(error.message);
  }
}

function schedulePreview() {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(refreshPreview, PREVIEW_DELAY);
}

async function loadNotes(query = '') {
  try {
    const path = query ? `/api/notes?q=${encodeURIComponent(query)}` : '/api/notes';
    const data = await api('GET', path);
    state.notes = data.notes;
    renderList();
  } catch (error) {
    setStatus(error.message);
  }
}

function openNote(note) {
  state.currentId = note ? note.id : null;
  els.title.value = note ? note.title : '';
  els.body.value = note ? note.body : '';
  els.remove.disabled = !note;
  markDirty(false);
  renderList();
  refreshPreview();
}

async function saveNote() {
  if (state.saving) return;
  const payload = { title: els.title.value, body: els.body.value };
  state.saving = true;
  els.save.disabled = true;
  try {
    const data = state.currentId
      ? await api('PUT', `/api/notes/${state.currentId}`, payload)
      : await api('POST', '/api/notes', payload);
    state.currentId = data.note.id;
    els.title.value = data.note.title;
    els.remove.disabled = false;
    markDirty(false);
    setStatus('Saved');
    await loadNotes(els.search.value.trim());
  } catch (error) {
    setStatus(error.message);
    els.save.disabled = false;
  } finally {
    state.saving = false;
  }
}

async function deleteNote() {
  if (!state.currentId) return;
  if (!window.confirm('Delete this note? This cannot be undone.')) return;
  try {
    await api('DELETE', `/api/notes/${state.currentId}`);
    state.currentId = null;
    await loadNotes(els.search.value.trim());
    openNote(state.notes[0] || null);
    setStatus('Deleted');
  } catch (error) {
    setStatus(error.message);
  }
}

els.list.addEventListener('click', (event) => {
  const item = event.target.closest('li[data-id]');
  if (!item) return;
  if (state.dirty && !window.confirm('Discard unsaved changes?')) return;
  const note = state.notes.find((n) => n.id === Number(item.dataset.id));
  if (note) openNote(note);
});

els.search.addEventListener('input', () => {
  clearTimeout(els.search.timer);
  els.search.timer = setTimeout(() => loadNotes(els.search.value.trim()), 200);
});

for (const field of [els.title, els.body]) {
  field.addEventListener('input', () => {
    markDirty(true);
    schedulePreview();
  });
}

els.save.addEventListener('click', saveNote);
els.remove.addEventListener('click', deleteNote);
els.create.addEventListener('click', () => {
  if (state.dirty && !window.confirm('Discard unsaved changes?')) return;
  openNote(null);
  els.title.focus();
});

document.addEventListener('keydown', (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 's') {
    event.preventDefault();
    if (state.dirty) saveNote();
  }
});

window.addEventListener('beforeunload', (event) => {
  if (state.dirty) {
    event.preventDefault();
    event.returnValue = '';
  }
});

(async function init() {
  await loadNotes();
  openNote(state.notes[0] || null);
})();
