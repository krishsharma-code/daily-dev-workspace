/* Feature Flags client — vanilla JS, no build step. */

const els = {
  status: document.getElementById("status"),
  createForm: document.getElementById("create-form"),
  list: document.getElementById("flag-list"),
  empty: document.getElementById("empty"),
  refresh: document.getElementById("refresh"),
  evaluateForm: document.getElementById("evaluate-form"),
  evalUser: document.getElementById("eval-user"),
  evalResults: document.getElementById("eval-results"),
};

let flags = [];

/* ---------------------------------------------------------------- helpers */

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: options.body ? { "Content-Type": "application/json" } : {},
    ...options,
  });
  if (response.status === 204) return null;
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || `request failed (${response.status})`);
  }
  return payload;
}

function setStatus(text, isError = false) {
  els.status.textContent = text;
  els.status.classList.toggle("error", isError);
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function parseAllowlist(raw) {
  return raw
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean);
}

/* ------------------------------------------------------------------- data */

async function loadFlags() {
  try {
    const payload = await api("/api/flags");
    flags = payload.flags;
    renderFlags();
    setStatus(`${payload.count} flag${payload.count === 1 ? "" : "s"}`);
  } catch (error) {
    setStatus(error.message, true);
  }
}

/* ------------------------------------------------------------------ render */

function renderFlags() {
  els.list.replaceChildren();
  els.empty.hidden = flags.length > 0;

  for (const flag of flags) {
    els.list.append(renderFlag(flag));
  }
}

function renderFlag(flag) {
  const item = el("li", "flag");

  const head = el("div", "flag-head");
  head.append(el("span", "flag-key", flag.key));
  head.append(el("span", `badge ${flag.enabled ? "on" : "off"}`, flag.enabled ? "on" : "off"));
  if (flag.description) head.append(el("span", "flag-desc", flag.description));
  item.append(head);

  const controls = el("div", "flag-controls");

  const switchLabel = el("label", "switch");
  const toggle = document.createElement("input");
  toggle.type = "checkbox";
  toggle.checked = flag.enabled;
  toggle.setAttribute("aria-label", `Enable ${flag.key}`);
  switchLabel.append(toggle, document.createTextNode("enabled"));

  const rollout = el("div", "rollout");
  const range = document.createElement("input");
  range.type = "range";
  range.min = "0";
  range.max = "100";
  range.step = "1";
  range.value = String(flag.rollout);
  range.setAttribute("aria-label", `Rollout for ${flag.key}`);
  const output = el("output", null, `${flag.rollout}%`);
  range.addEventListener("input", () => {
    output.textContent = `${range.value}%`;
  });
  rollout.append(range, output);

  const save = el("button", "primary", "Save");
  save.type = "button";

  controls.append(switchLabel, rollout, save);
  item.append(controls);

  const foot = el("div", "flag-foot");
  const allowlist = document.createElement("input");
  allowlist.type = "text";
  allowlist.value = flag.allowlist.join(", ");
  allowlist.placeholder = "allowlist: user-1, user-2";
  allowlist.setAttribute("aria-label", `Allowlist for ${flag.key}`);

  const remove = el("button", "danger", "Delete");
  remove.type = "button";

  save.addEventListener("click", async () => {
    try {
      const payload = await api(`/api/flags/${encodeURIComponent(flag.key)}`, {
        method: "PATCH",
        body: JSON.stringify({
          enabled: toggle.checked,
          rollout: Number(range.value),
          allowlist: parseAllowlist(allowlist.value),
        }),
      });
      replaceFlag(payload.flag);
      setStatus(`saved ${flag.key}`);
    } catch (error) {
      setStatus(error.message, true);
    }
  });

  remove.addEventListener("click", async () => {
    if (!confirm(`Delete flag "${flag.key}"?`)) return;
    try {
      await api(`/api/flags/${encodeURIComponent(flag.key)}`, { method: "DELETE" });
      flags = flags.filter((entry) => entry.key !== flag.key);
      renderFlags();
      setStatus(`deleted ${flag.key}`);
    } catch (error) {
      setStatus(error.message, true);
    }
  });

  foot.append(allowlist, remove);
  item.append(foot);
  return item;
}

function replaceFlag(updated) {
  flags = flags.map((flag) => (flag.key === updated.key ? updated : flag));
  renderFlags();
}

/* ------------------------------------------------------------------ events */

els.createForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(els.createForm);
  try {
    const payload = await api("/api/flags", {
      method: "POST",
      body: JSON.stringify({
        key: String(form.get("key") || ""),
        description: String(form.get("description") || ""),
        rollout: Number(form.get("rollout") || 0),
        allowlist: parseAllowlist(String(form.get("allowlist") || "")),
      }),
    });
    replaceFlag(payload.flag);
    els.createForm.reset();
    els.createForm.elements.rollout.value = "0";
    setStatus(`created ${payload.flag.key}`);
  } catch (error) {
    setStatus(error.message, true);
  }
});

els.refresh.addEventListener("click", loadFlags);

els.evaluateForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const userId = els.evalUser.value.trim();
  if (!userId) return;
  try {
    const payload = await api(`/api/evaluate/all?user_id=${encodeURIComponent(userId)}`);
    renderEvaluations(payload);
  } catch (error) {
    setStatus(error.message, true);
  }
});

function renderEvaluations(payload) {
  els.evalResults.replaceChildren();
  if (!payload.count) {
    els.evalResults.append(el("li", null, "No flags to evaluate yet."));
    return;
  }
  for (const decision of payload.flags) {
    const row = el("li");
    row.append(el("span", "flag-key", decision.key));
    row.append(
      el("span", `verdict ${decision.enabled ? "on" : "off"}`, decision.enabled ? "ON" : "OFF")
    );
    row.append(el("span", "why", `${decision.reason} · bucket ${decision.bucket}`));
    els.evalResults.append(row);
  }
  setStatus(`evaluated ${payload.count} flag${payload.count === 1 ? "" : "s"} for ${payload.user_id}`);
}

loadFlags();
