/**
 * Streak Board client.
 *
 * Talks to the same-origin API (see ../README.md) and paints a two-week window
 * of days per habit. All streak arithmetic lives on the server; this file only
 * decides what a day looks like: done, open, not due, or still in the future.
 */

const WEEKDAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const WINDOW_DAYS = 14;

const state = {
  today: null,
  /** Days the visible window ends on; shifts backwards and forwards. */
  anchor: null,
  habits: [],
  counts: { habits: 0, done_today: 0, due_today: 0 },
};

const els = {
  form: document.getElementById("habit-form"),
  weekdayGrid: document.getElementById("weekday-grid"),
  habitList: document.getElementById("habit-list"),
  boardEmpty: document.getElementById("board-empty"),
  boardMeta: document.getElementById("board-meta"),
  prevWeek: document.getElementById("prev-week"),
  nextWeek: document.getElementById("next-week"),
  todayBtn: document.getElementById("today-btn"),
  toast: document.getElementById("toast"),
};

// ---------------------------------------------------------------- utilities

function addDays(isoDay, delta) {
  const [year, month, day] = isoDay.split("-").map(Number);
  const date = new Date(Date.UTC(year, month - 1, day));
  date.setUTCDate(date.getUTCDate() + delta);
  return date.toISOString().slice(0, 10);
}

function isoWeekday(isoDay) {
  const [year, month, day] = isoDay.split("-").map(Number);
  // getUTCDay() is 0 = Sunday; the server counts Monday as 0.
  return (new Date(Date.UTC(year, month - 1, day)).getUTCDay() + 6) % 7;
}

function prettyDate(isoDay) {
  const [year, month, day] = isoDay.split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, day)).toLocaleDateString("en-IN", {
    day: "numeric",
    month: "short",
    timeZone: "UTC",
  });
}

let toastTimer = null;
function toast(message, isError = false) {
  els.toast.textContent = message;
  els.toast.classList.toggle("toast--error", isError);
  els.toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    els.toast.hidden = true;
  }, 2600);
}

async function api(method, path, body) {
  const response = await fetch(path, {
    method,
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  const payload = text ? JSON.parse(text) : {};
  if (!response.ok) {
    throw new Error(payload.error || `request failed (${response.status})`);
  }
  return payload;
}

// ---------------------------------------------------------------- rendering

function buildWeekdayPicker() {
  els.weekdayGrid.innerHTML = "";
  WEEKDAY_LABELS.forEach((label, index) => {
    const wrapper = document.createElement("label");
    wrapper.className = "weekday";
    wrapper.innerHTML = `<input type="checkbox" name="weekday" value="${index}" checked /><span>${label.slice(0, 2)}</span>`;
    wrapper.querySelector("input").addEventListener("change", (event) => {
      // Never let the last due day be unticked: a habit has to be due sometime.
      const boxes = [...els.weekdayGrid.querySelectorAll("input")];
      if (boxes.every((box) => !box.checked)) {
        event.target.checked = true;
      }
    });
    els.weekdayGrid.append(wrapper);
  });
}

function selectedWeekdays() {
  return [...els.weekdayGrid.querySelectorAll("input:checked")].map((box) =>
    Number(box.value),
  );
}

function dueLabel(weekdays) {
  if (weekdays.length === 7) return "Every day";
  if (weekdays.length === 5 && weekdays.every((day) => day < 5)) {
    return "Weekdays";
  }
  if (weekdays.length === 2 && weekdays[0] === 5 && weekdays[1] === 6) {
    return "Weekends";
  }
  return weekdays.map((day) => WEEKDAY_LABELS[day]).join(", ");
}

function dayState(habit, isoDay) {
  if (isoDay > state.today) return "future";
  if (!habit.weekdays.includes(isoWeekday(isoDay))) return "off";
  return habit.days.includes(isoDay) ? "done" : "open";
}

function renderDays(habit) {
  const container = document.createElement("div");
  container.className = "habit__days";
  const start = addDays(state.anchor, -(WINDOW_DAYS - 1));

  for (let offset = 0; offset < WINDOW_DAYS; offset += 1) {
    const isoDay = addDays(start, offset);
    const status = dayState(habit, isoDay);
    const button = document.createElement("button");
    button.type = "button";
    button.className = "day";
    button.dataset.state = status;
    button.dataset.day = isoDay;
    if (isoDay === state.today) button.classList.add("day--today");

    const weekday = WEEKDAY_LABELS[isoWeekday(isoDay)];
    button.title = `${weekday}, ${prettyDate(isoDay)}`;
    button.innerHTML = `<span>${weekday.slice(0, 1)}</span><span class="day__num">${Number(isoDay.slice(-2))}</span>`;

    if (status === "done") {
      button.setAttribute("aria-pressed", "true");
      button.setAttribute(
        "aria-label",
        `${prettyDate(isoDay)}: done. Activate to undo.`,
      );
      button.addEventListener("click", () => toggle(habit, isoDay, true));
    } else if (status === "open") {
      button.setAttribute("aria-pressed", "false");
      button.setAttribute(
        "aria-label",
        `${prettyDate(isoDay)}: not done. Activate to check in.`,
      );
      button.addEventListener("click", () => toggle(habit, isoDay, false));
    } else if (status === "off") {
      button.disabled = true;
      button.title = "Not a due day for this habit";
      button.setAttribute("aria-label", `${prettyDate(isoDay)}: not due`);
    } else {
      button.disabled = true;
      button.setAttribute("aria-label", `${prettyDate(isoDay)}: in the future`);
    }
    container.append(button);
  }
  return container;
}

function renderHabit(habit) {
  const item = document.createElement("li");
  item.className = "habit";

  const info = document.createElement("div");
  info.className = "habit__info";
  const name = document.createElement("div");
  name.className = "habit__name";
  name.textContent = habit.name;
  const due = document.createElement("p");
  due.className = "habit__due";
  due.textContent = `Due ${dueLabel(habit.weekdays)}`;
  info.append(name, due);

  const summary = habit.summary;
  const stats = document.createElement("div");
  stats.className = "habit__stats";
  stats.innerHTML = `
    <div class="stat stat--streak">
      <span class="stat__value">${summary.current}</span>
      <span class="stat__label">streak</span>
    </div>
    <div class="stat">
      <span class="stat__value">${summary.longest}</span>
      <span class="stat__label">best</span>
    </div>
    <div class="stat">
      <span class="stat__value">${summary.window.percent}%</span>
      <span class="stat__label">${summary.window.days}d</span>
    </div>
  `;

  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "btn btn--danger habit__delete";
  remove.textContent = "Delete";
  remove.addEventListener("click", () => deleteHabit(habit));

  item.append(info, stats, renderDays(habit), remove);
  return item;
}

function render() {
  els.habitList.innerHTML = "";
  const hasHabits = state.habits.length > 0;
  els.boardEmpty.hidden = hasHabits;

  state.habits.forEach((habit) => els.habitList.append(renderHabit(habit)));

  const { habits, done_today: done, due_today: due } = state.counts;
  els.boardMeta.textContent = habits
    ? `${done} of ${due} due today · ${habits} habit${habits === 1 ? "" : "s"} · window ends ${prettyDate(state.anchor)}`
    : "No habits yet";

  els.nextWeek.disabled = state.anchor >= state.today;
}

// ---------------------------------------------------------------- actions

async function refresh() {
  const board = await api("GET", "/api/habits");
  state.today = board.today;
  if (!state.anchor || state.anchor > board.today) {
    state.anchor = board.today;
  }
  state.habits = board.habits;
  state.counts = board.counts;
  render();
}

async function toggle(habit, isoDay, isDone) {
  try {
    if (isDone) {
      await api(
        "DELETE",
        `/api/habits/${encodeURIComponent(habit.id)}/checkins/${isoDay}`,
      );
    } else {
      await api(
        "POST",
        `/api/habits/${encodeURIComponent(habit.id)}/checkins`,
        { day: isoDay },
      );
    }
    await refresh();
  } catch (error) {
    toast(error.message, true);
  }
}

async function deleteHabit(habit) {
  const sure = window.confirm(
    `Delete "${habit.name}" and its ${habit.days.length} check-in(s)?`,
  );
  if (!sure) return;
  try {
    await api("DELETE", `/api/habits/${encodeURIComponent(habit.id)}`);
    toast(`Deleted ${habit.name}`);
    await refresh();
  } catch (error) {
    toast(error.message, true);
  }
}

async function createHabit(event) {
  event.preventDefault();
  const data = new FormData(els.form);
  const name = String(data.get("name") || "").trim();
  const weekdays = selectedWeekdays();
  if (!name) {
    toast("Give the habit a name first", true);
    return;
  }
  try {
    await api("POST", "/api/habits", { name, weekdays });
    els.form.reset();
    els.weekdayGrid
      .querySelectorAll("input")
      .forEach((box) => (box.checked = true));
    toast(`Added ${name}`);
    await refresh();
  } catch (error) {
    toast(error.message, true);
  }
}

function shiftWindow(days) {
  const next = addDays(state.anchor, days);
  state.anchor = next > state.today ? state.today : next;
  render();
}

// ---------------------------------------------------------------- boot

function boot() {
  buildWeekdayPicker();
  els.form.addEventListener("submit", createHabit);
  els.prevWeek.addEventListener("click", () => shiftWindow(-7));
  els.nextWeek.addEventListener("click", () => shiftWindow(7));
  els.todayBtn.addEventListener("click", () => {
    state.anchor = state.today;
    render();
  });

  refresh().catch((error) => {
    els.boardMeta.textContent = "Could not reach the server";
    toast(error.message, true);
  });
}

boot();
