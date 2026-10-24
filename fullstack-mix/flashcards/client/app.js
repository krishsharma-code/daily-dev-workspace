// Flashcards front end.
//
// Talks to the same-origin JSON API served by `server/app.py`. When the page
// is opened straight from disk (`file://`) it falls back to localhost:8000 so
// the demo still works without running the server's static handler.

const BASE = location.protocol === "file:" ? "http://localhost:8000" : "";

async function api(path, options = {}) {
  const response = await fetch(`${BASE}${path}`, {
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

function toast(message, isError = false) {
  const el = document.getElementById("toast");
  el.textContent = message;
  el.classList.toggle("toast-error", isError);
  el.hidden = false;
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => {
    el.hidden = true;
  }, 2600);
}

// -- state ------------------------------------------------------------------

let dueQueue = [];
let current = null;
let activeDeck = "";

// -- rendering --------------------------------------------------------------

function deckQuery() {
  return activeDeck ? `?deck=${encodeURIComponent(activeDeck)}` : "";
}

async function refresh() {
  const [health, decks, cards] = await Promise.all([
    api("/api/health"),
    api("/api/decks"),
    api(`/api/cards${deckQuery()}`),
  ]);

  document.getElementById("stats").textContent =
    `${health.cards} card${health.cards === 1 ? "" : "s"} · ${health.due} due`;

  renderDeckFilter(decks.decks);
  renderCards(cards.cards);
}

function renderDeckFilter(decks) {
  const select = document.getElementById("deck-filter");
  const previous = select.value;
  select.innerHTML = '<option value="">All decks</option>';
  for (const deck of decks) {
    const option = document.createElement("option");
    option.value = deck.deck;
    option.textContent = `${deck.deck} (${deck.due}/${deck.total} due)`;
    select.appendChild(option);
  }
  select.value = previous;
  activeDeck = select.value;
}

function renderCards(cards) {
  const list = document.getElementById("card-list");
  list.innerHTML = "";
  document.getElementById("list-empty").hidden = cards.length > 0;
  for (const card of cards) {
    const item = document.createElement("li");
    item.className = "card-row";

    const main = document.createElement("div");
    main.className = "card-main";
    const front = document.createElement("p");
    front.className = "card-front";
    front.textContent = card.front;
    const back = document.createElement("p");
    back.className = "card-back";
    back.textContent = card.back;
    const meta = document.createElement("p");
    meta.className = "card-meta";
    meta.textContent =
      `${card.deck} · interval ${card.interval_days}d · ease ${card.ease} · ` +
      `${card.repetitions} reps · ${card.lapses} lapses · due ${card.due.slice(0, 10)}`;
    main.append(front, back, meta);

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "danger";
    remove.textContent = "Delete";
    remove.addEventListener("click", async () => {
      try {
        await api(`/api/cards/${card.id}`, { method: "DELETE" });
        toast("Card deleted");
        await refresh();
      } catch (error) {
        toast(error.message, true);
      }
    });

    item.append(main, remove);
    list.appendChild(item);
  }
}

// -- study session ----------------------------------------------------------

async function startStudy() {
  const data = await api(`/api/cards/due${deckQuery()}`);
  dueQueue = data.cards;
  if (dueQueue.length === 0) {
    document.getElementById("study-area").hidden = true;
    document.getElementById("study-empty").hidden = false;
    toast("Nothing due to study");
    return;
  }
  nextCard();
}

function nextCard() {
  const area = document.getElementById("study-area");
  if (dueQueue.length === 0) {
    area.hidden = true;
    document.getElementById("study-empty").hidden = false;
    toast("Session complete");
    return;
  }
  current = dueQueue.shift();
  document.getElementById("study-empty").hidden = true;
  area.hidden = false;
  document.getElementById("study-deck").textContent = current.deck;
  document.getElementById("study-front").textContent = current.front;
  const back = document.getElementById("study-back");
  back.textContent = current.back;
  back.hidden = true;
  document.getElementById("show-answer").hidden = false;
  document.getElementById("quality-buttons").hidden = true;
}

function revealAnswer() {
  document.getElementById("study-back").hidden = false;
  document.getElementById("show-answer").hidden = true;
  document.getElementById("quality-buttons").hidden = false;
}

async function grade(quality) {
  if (!current) return;
  try {
    const result = await api(`/api/cards/${current.id}/review`, {
      method: "POST",
      body: JSON.stringify({ quality }),
    });
    toast(`Next review in ${result.card.interval_days} day(s)`);
    await refresh();
    nextCard();
  } catch (error) {
    toast(error.message, true);
  }
}

// -- wiring -----------------------------------------------------------------

function init() {
  document.getElementById("add-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const deck = document.getElementById("deck").value;
    const front = document.getElementById("front").value;
    const back = document.getElementById("back").value;
    try {
      await api("/api/cards", {
        method: "POST",
        body: JSON.stringify({ deck, front, back }),
      });
      event.target.reset();
      toast("Card added");
      await refresh();
    } catch (error) {
      toast(error.message, true);
    }
  });

  document.getElementById("deck-filter").addEventListener("change", async (event) => {
    activeDeck = event.target.value;
    await refresh();
  });

  document.getElementById("start-study").addEventListener("click", () => {
    startStudy().catch((error) => toast(error.message, true));
  });
  document.getElementById("show-answer").addEventListener("click", revealAnswer);
  document.getElementById("quality-buttons").addEventListener("click", (event) => {
    const button = event.target.closest("[data-quality]");
    if (button) grade(Number(button.dataset.quality));
  });

  refresh().catch((error) => toast(error.message, true));
}

if (typeof document !== "undefined") {
  document.addEventListener("DOMContentLoaded", init);
}
