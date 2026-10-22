/* Link Vault front end.
 *
 * Plain ES2020, no build step and no dependencies. When the page is served by
 * the Python server (the normal case) the API lives on the same origin. When
 * you open this file directly from disk, it falls back to the local server so
 * the demo still works.
 */

const API_BASE = location.protocol === "file:" ? "http://localhost:8000" : "";

const els = {
  form: document.getElementById("link-form"),
  url: document.getElementById("url"),
  title: document.getElementById("title"),
  tags: document.getElementById("tags"),
  status: document.getElementById("form-status"),
  search: document.getElementById("search"),
  tagFilter: document.getElementById("tag-filter"),
  refresh: document.getElementById("refresh"),
  count: document.getElementById("count"),
  list: document.getElementById("links"),
};

/** Split a comma separated tag string into a clean array. */
function parseTags(value) {
  return [...new Set(value.split(",").map((t) => t.trim().toLowerCase()).filter(Boolean))];
}

async function api(path, options = {}) {
  const response = await fetch(API_BASE + path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (response.status === 204) return null;
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.error || `Request failed (${response.status})`);
  }
  return data;
}

function setStatus(message, isError = false) {
  els.status.textContent = message;
  els.status.classList.toggle("error", isError);
}

function renderLinks(links) {
  els.list.innerHTML = "";
  els.count.textContent = links.length === 1 ? "1 link" : `${links.length} links`;

  if (links.length === 0) {
    const empty = document.createElement("li");
    empty.className = "empty";
    empty.textContent = "No links yet — add one above.";
    els.list.appendChild(empty);
    return;
  }

  for (const link of links) {
    const item = document.createElement("li");
    item.className = "link";

    const info = document.createElement("div");
    const anchor = document.createElement("a");
    anchor.href = link.url;
    anchor.target = "_blank";
    anchor.rel = "noopener noreferrer";
    anchor.textContent = link.title;

    const urlLine = document.createElement("span");
    urlLine.className = "url";
    urlLine.textContent = link.url;

    info.append(anchor, urlLine);

    if (link.tags.length) {
      const tagRow = document.createElement("div");
      tagRow.className = "tags";
      for (const tag of link.tags) {
        const chip = document.createElement("span");
        chip.className = "tag";
        chip.textContent = tag;
        tagRow.appendChild(chip);
      }
      info.appendChild(tagRow);
    }

    const remove = document.createElement("button");
    remove.className = "remove";
    remove.type = "button";
    remove.textContent = "Delete";
    remove.addEventListener("click", () => deleteLink(link.id));

    item.append(info, remove);
    els.list.appendChild(item);
  }
}

async function loadLinks() {
  const params = new URLSearchParams();
  if (els.search.value.trim()) params.set("q", els.search.value.trim());
  if (els.tagFilter.value.trim()) params.set("tag", els.tagFilter.value.trim());
  const suffix = params.toString() ? `?${params}` : "";
  try {
    const data = await api(`/api/links${suffix}`);
    renderLinks(data.links);
  } catch (err) {
    setStatus(err.message, true);
  }
}

async function deleteLink(id) {
  try {
    await api(`/api/links/${id}`, { method: "DELETE" });
    await loadLinks();
  } catch (err) {
    setStatus(err.message, true);
  }
}

els.form.addEventListener("submit", async (event) => {
  event.preventDefault();
  setStatus("Saving…");
  try {
    await api("/api/links", {
      method: "POST",
      body: JSON.stringify({
        url: els.url.value,
        title: els.title.value,
        tags: parseTags(els.tags.value),
      }),
    });
    els.form.reset();
    setStatus("Saved.");
    await loadLinks();
  } catch (err) {
    setStatus(err.message, true);
  }
});

let debounce;
function scheduleReload() {
  clearTimeout(debounce);
  debounce = setTimeout(loadLinks, 200);
}

els.search.addEventListener("input", scheduleReload);
els.tagFilter.addEventListener("input", scheduleReload);
els.refresh.addEventListener("click", loadLinks);

loadLinks();
