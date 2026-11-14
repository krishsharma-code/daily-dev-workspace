/* Slot Finder client — vanilla JS, no build step. */
"use strict";

const form = document.getElementById("slot-form");
const busyInput = document.getElementById("busy");
const startInput = document.getElementById("start-date");
const endInput = document.getElementById("end-date");
const output = document.getElementById("output");
const summary = document.getElementById("summary");
const errorBox = document.getElementById("error");

/* ------------------------------------------------------------------ */
/* Dates                                                               */
/* ------------------------------------------------------------------ */

function isoDate(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function seedDates() {
  const today = new Date();
  const from = new Date(today.getTime());
  from.setDate(from.getDate() + 1);
  const to = new Date(from.getTime());
  to.setDate(to.getDate() + 6);
  startInput.value = isoDate(from);
  endInput.value = isoDate(to);
}

/** "2026-11-16T09:00:00+05:30" -> "09:00" (wall clock, as stored). */
function wallClock(iso) {
  return typeof iso === "string" && iso.length >= 16 ? iso.slice(11, 16) : iso;
}

/* ------------------------------------------------------------------ */
/* Request                                                             */
/* ------------------------------------------------------------------ */

function collectPayload() {
  const weekdays = Array.from(
    form.querySelectorAll('input[name="weekday"]:checked'),
    (box) => box.value
  );
  return {
    busy: busyInput.value,
    timezone: document.getElementById("timezone").value.trim(),
    start_date: startInput.value,
    end_date: endInput.value,
    work_start: document.getElementById("work-start").value,
    work_end: document.getElementById("work-end").value,
    weekdays: weekdays,
    duration_min: Number(document.getElementById("duration").value),
    step_min: Number(document.getElementById("step").value),
    limit: Number(document.getElementById("limit").value),
  };
}

async function search(payload) {
  const response = await fetch("/api/slots", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || `request failed (${response.status})`);
  }
  return data;
}

/* ------------------------------------------------------------------ */
/* Rendering                                                           */
/* ------------------------------------------------------------------ */

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function slotChip(slot) {
  const chip = el("button", "chip");
  chip.type = "button";
  chip.title = `${slot.start} → ${slot.end}\nUTC ${slot.start_utc} → ${slot.end_utc}\nClick to copy`;
  chip.textContent = `${wallClock(slot.start)}–${wallClock(slot.end)}`;

  chip.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(slot.start);
    } catch (error) {
      const scratch = document.createElement("textarea");
      scratch.value = slot.start;
      document.body.appendChild(scratch);
      scratch.select();
      document.execCommand("copy");
      scratch.remove();
    }
    chip.classList.add("copied");
    const label = chip.textContent;
    chip.textContent = "copied";
    window.setTimeout(() => {
      chip.textContent = label;
      chip.classList.remove("copied");
    }, 900);
  });

  return chip;
}

function renderDay(day, slots) {
  const card = el("div", "day");
  const head = el("div", "day-head");
  head.appendChild(el("span", "", `${day.weekday} ${day.date}`));

  if (!day.included) {
    head.appendChild(el("span", "meta", day.reason || "skipped"));
    card.appendChild(head);
    return card;
  }

  const hours = (day.free_minutes / 60).toFixed(day.free_minutes % 60 ? 1 : 0);
  head.appendChild(el("span", "meta", `${hours} h free`));
  card.appendChild(head);

  if (!slots.length) {
    card.appendChild(el("p", "day-skip", "No slot fits on this day."));
    return card;
  }

  const chips = el("div", "chips");
  slots.forEach((slot) => chips.appendChild(slotChip(slot)));
  card.appendChild(chips);
  return card;
}

function render(result) {
  output.replaceChildren();

  const byDate = new Map();
  result.slots.forEach((slot) => {
    if (!byDate.has(slot.date)) byDate.set(slot.date, []);
    byDate.get(slot.date).push(slot);
  });

  result.days.forEach((day) => {
    output.appendChild(renderDay(day, byDate.get(day.date) || []));
  });

  const count = result.slots.length;
  const noun = count === 1 ? "slot" : "slots";
  const notes = [];
  if (count) {
    notes.push(`${wallClock(result.slots[0].start)}–${wallClock(result.slots[count - 1].end)}`);
  }
  if (result.truncated) notes.push("stopped at the limit");

  const summaryNode = el(
    "span",
    result.truncated ? "truncated" : "",
    `${count} ${noun} in ${result.timezone}${notes.length ? ` (${notes.join(", ")})` : ""}`
  );
  summary.replaceChildren(summaryNode);

  output.appendChild(
    el(
      "p",
      "footnote",
      `Times are wall-clock in ${result.timezone}; hover a slot for its UTC instant. Grid anchored at ${result.window.work_start}.`
    )
  );
}

/* ------------------------------------------------------------------ */
/* Wiring                                                              */
/* ------------------------------------------------------------------ */

function showError(message) {
  errorBox.textContent = message;
  errorBox.hidden = !message;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  showError("");

  const payload = collectPayload();
  if (!payload.start_date || !payload.end_date) {
    showError("Pick a date range first.");
    return;
  }
  if (!payload.weekdays.length) {
    showError("Tick at least one working day.");
    return;
  }

  summary.textContent = "Searching…";
  try {
    render(await search(payload));
  } catch (error) {
    summary.textContent = "Search failed.";
    showError(error.message);
  }
});

document.getElementById("load-example").addEventListener("click", async () => {
  showError("");
  try {
    const response = await fetch("/api/example");
    const data = await response.json();
    const example = data.request;
    busyInput.value = example.busy;
    document.getElementById("timezone").value = example.timezone;
    startInput.value = example.start_date;
    endInput.value = example.end_date;
    document.getElementById("work-start").value = example.work_start;
    document.getElementById("work-end").value = example.work_end;
    document.getElementById("duration").value = example.duration_min;
    document.getElementById("step").value = example.step_min;
    document.getElementById("limit").value = example.limit;
    form.querySelectorAll('input[name="weekday"]').forEach((box) => {
      box.checked = example.weekdays.includes(box.value);
    });
    form.requestSubmit();
  } catch (error) {
    showError("Could not load the example — is the server running?");
  }
});

document.getElementById("clear-busy").addEventListener("click", () => {
  busyInput.value = "";
  busyInput.focus();
});

seedDates();
