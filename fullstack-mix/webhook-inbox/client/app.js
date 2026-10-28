/* Webhook Inbox client.
 *
 * Talks to the stdlib server in ../server: bins at /api/bins, events at
 * /api/events. No build step, no dependencies - open the page from the same
 * origin the server serves it from.
 */

const POLL_MS = 4000;

const state = {
  bins: [],
  selectedBin: null, // null means "all bins"
  events: [],
  selectedEventId: null,
  selectedEvent: null,
};

const el = {
  status: document.getElementById("status"),
  refresh: document.getElementById("refresh"),
  autoRefresh: document.getElementById("auto-refresh"),
  binList: document.getElementById("bin-list"),
  binForm: document.getElementById("bin-form"),
  binName: document.getElementById("bin-name"),
  binHint: document.getElementById("bin-hint"),
  captureUrl: document.getElementById("capture-url"),
  binTemplate: document.getElementById("bin-template"),
  eventList: document.getElementById("event-list"),
  eventCount: document.getElementById("event-count"),
  eventEmpty: document.getElementById("event-empty"),
  eventTemplate: document.getElementById("event-template"),
  detailBody: document.getElementById("detail-body"),
  deleteEvent: document.getElementById("delete-event"),
};

/* ------------------------------------------------------------------ helpers */

function setStatus(text, stateName) {
  el.status.textContent = text;
  el.status.dataset.state = stateName;
}

function formatTime(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function formatBytes(count) {
  if (count < 1024) return `${count} B`;
  if (count < 1024 * 1024) return `${(count / 1024).toFixed(1)} KB`;
  return `${(count / (1024 * 1024)).toFixed(1)} MB`;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (response.status === 204) return null;
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || `request failed (${response.status})`);
  }
  return payload;
}

/* ----------------------------------------------------------------- rendering */

function renderBins() {
  el.binList.replaceChildren();

  const all = el.binTemplate.content.firstElementChild.cloneNode(true);
  all.querySelector(".bin-name").textContent = "All bins";
  all.querySelector(".bin-count").textContent = String(
    state.bins.reduce((total, bin) => total + bin.events, 0)
  );
  all.querySelector(".bin-clear").remove();
  all.setAttribute("aria-current", String(state.selectedBin === null));
  all.querySelector(".bin-select").addEventListener("click", () => selectBin(null));
  el.binList.append(all);

  for (const bin of state.bins) {
    const node = el.binTemplate.content.firstElementChild.cloneNode(true);
    node.querySelector(".bin-name").textContent = bin.name;
    node.querySelector(".bin-count").textContent = String(bin.events);
    node.setAttribute("aria-current", String(state.selectedBin === bin.name));
    node.querySelector(".bin-select").addEventListener("click", () => selectBin(bin.name));
    node.querySelector(".bin-clear").addEventListener("click", (event) => {
      event.stopPropagation();
      clearBin(bin.name);
    });
    el.binList.append(node);
  }

  const target = state.selectedBin ?? "<bin>";
  el.captureUrl.textContent = `${location.origin}/in/${target}`;
}

function renderEvents() {
  el.eventList.replaceChildren();
  el.eventCount.textContent = String(state.events.length);
  el.eventEmpty.hidden = state.events.length > 0;

  for (const event of state.events) {
    const node = el.eventTemplate.content.firstElementChild.cloneNode(true);
    node.querySelector(".event-method").textContent = event.method;
    node.querySelector(".event-path").textContent = event.path;
    node.querySelector(".event-time").textContent = formatTime(event.received_at);
    node.setAttribute("aria-current", String(state.selectedEventId === event.id));
    node.querySelector(".event-select").addEventListener("click", () => selectEvent(event.id));
    el.eventList.append(node);
  }
}

function renderDetail() {
  const event = state.selectedEvent;
  el.detailBody.replaceChildren();

  if (!event) {
    el.deleteEvent.hidden = true;
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "Select an event to inspect it.";
    el.detailBody.append(empty);
    return;
  }

  el.deleteEvent.hidden = false;

  const meta = document.createElement("dl");
  meta.className = "detail-meta";
  const rows = [
    ["Method", event.method],
    ["Path", event.path],
    ["Received", event.received_at],
    ["Size", formatBytes(event.body_size) + (event.truncated ? " (truncated)" : "")],
  ];
  for (const [label, value] of rows) {
    const dt = document.createElement("dt");
    dt.textContent = label;
    const dd = document.createElement("dd");
    dd.textContent = value;
    meta.append(dt, dd);
  }
  el.detailBody.append(meta);

  const bodyTitle = document.createElement("h3");
  bodyTitle.className = "section-title";
  bodyTitle.textContent = event.body_encoding === "base64" ? "Body (base64)" : "Body";
  const pre = document.createElement("pre");
  pre.className = "body";
  pre.textContent = event.body || "(empty)";
  el.detailBody.append(bodyTitle, pre);

  const headerTitle = document.createElement("h3");
  headerTitle.className = "section-title";
  headerTitle.textContent = "Headers";
  const table = document.createElement("table");
  table.className = "headers";
  const names = Object.keys(event.headers || {}).sort();
  if (names.length === 0) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.textContent = "(none)";
    row.append(cell);
    table.append(row);
  }
  for (const name of names) {
    const row = document.createElement("tr");
    const th = document.createElement("th");
    th.textContent = name;
    const td = document.createElement("td");
    td.textContent = event.headers[name];
    row.append(th, td);
    table.append(row);
  }
  el.detailBody.append(headerTitle, table);

  el.detailBody.append(buildReplayForm(event));
}

function buildReplayForm(event) {
  const form = document.createElement("form");
  form.className = "replay";

  const label = document.createElement("label");
  label.textContent = "Replay to URL";
  label.htmlFor = "replay-target";

  const row = document.createElement("div");
  row.className = "row";
  const input = document.createElement("input");
  input.id = "replay-target";
  input.placeholder = "http://localhost:3000/webhook";
  input.required = true;
  const submit = document.createElement("button");
  submit.className = "button";
  submit.type = "submit";
  submit.textContent = "Replay";
  row.append(input, submit);

  const result = document.createElement("p");
  result.className = "replay-result";

  form.append(label, row, result);

  form.addEventListener("submit", async (submitEvent) => {
    submitEvent.preventDefault();
    submit.disabled = true;
    result.dataset.ok = "";
    result.textContent = "Replaying…";
    try {
      const payload = await api(`/api/events/${event.id}/replay`, {
        method: "POST",
        body: JSON.stringify({ target: input.value.trim() }),
      });
      const ok = payload.upstream_status >= 200 && payload.upstream_status < 300;
      result.dataset.ok = String(ok);
      result.textContent =
        `Upstream responded ${payload.upstream_status}` +
        (payload.upstream_body ? `\n${payload.upstream_body}` : "");
    } catch (error) {
      result.dataset.ok = "false";
      result.textContent = error.message;
    } finally {
      submit.disabled = false;
    }
  });

  return form;
}

/* -------------------------------------------------------------------- actions */

async function loadBins() {
  const payload = await api("/api/bins");
  state.bins = payload.bins || [];
  if (state.selectedBin && !state.bins.some((bin) => bin.name === state.selectedBin)) {
    state.selectedBin = null;
  }
  renderBins();
}

async function loadEvents() {
  const query = new URLSearchParams({ limit: "100" });
  if (state.selectedBin) query.set("bin", state.selectedBin);
  const payload = await api(`/api/events?${query.toString()}`);
  state.events = payload.events || [];
  if (state.selectedEventId && !state.events.some((e) => e.id === state.selectedEventId)) {
    state.selectedEventId = null;
    state.selectedEvent = null;
    renderDetail();
  }
  renderEvents();
}

async function loadEvent(eventId) {
  const payload = await api(`/api/events/${eventId}`);
  state.selectedEvent = payload.event;
  renderDetail();
}

async function refresh() {
  try {
    await Promise.all([loadBins(), loadEvents()]);
    setStatus("connected", "ok");
  } catch (error) {
    setStatus(error.message, "error");
  }
}

function selectBin(name) {
  state.selectedBin = name;
  state.selectedEventId = null;
  state.selectedEvent = null;
  renderBins();
  renderDetail();
  loadEvents().catch((error) => setStatus(error.message, "error"));
}

async function selectEvent(eventId) {
  state.selectedEventId = eventId;
  renderEvents();
  try {
    await loadEvent(eventId);
  } catch (error) {
    setStatus(error.message, "error");
  }
}

async function clearBin(name) {
  if (!confirm(`Delete every event in "${name}"?`)) return;
  try {
    await api(`/api/bins/${encodeURIComponent(name)}/clear`, { method: "POST", body: "{}" });
    state.selectedEventId = null;
    state.selectedEvent = null;
    renderDetail();
    await refresh();
  } catch (error) {
    setStatus(error.message, "error");
  }
}

async function deleteSelected() {
  if (!state.selectedEventId) return;
  try {
    await api(`/api/events/${state.selectedEventId}`, { method: "DELETE" });
    state.selectedEventId = null;
    state.selectedEvent = null;
    renderDetail();
    await refresh();
  } catch (error) {
    setStatus(error.message, "error");
  }
}

/* ----------------------------------------------------------------------- boot */

el.refresh.addEventListener("click", refresh);
el.deleteEvent.addEventListener("click", deleteSelected);

el.binForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const name = el.binName.value.trim();
  if (!name) return;
  try {
    await api("/api/bins", { method: "POST", body: JSON.stringify({ name }) });
    el.binName.value = "";
    await refresh();
  } catch (error) {
    setStatus(error.message, "error");
  }
});

refresh();

setInterval(() => {
  if (el.autoRefresh.checked && !document.hidden) refresh();
}, POLL_MS);
