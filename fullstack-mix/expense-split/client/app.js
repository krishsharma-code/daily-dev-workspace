/**
 * Expense Split client.
 *
 * Plain ES modules, no build step: open the page served by `server/app.py` and
 * this talks to the same origin at `/api/...`.
 */

const state = {
  groups: [],
  currentId: null,
  group: null,
  expenses: [],
  settlement: null,
};

const els = {
  groupForm: document.getElementById("group-form"),
  groupList: document.getElementById("group-list"),
  groupEmpty: document.getElementById("group-empty"),
  workspaceEmpty: document.getElementById("workspace-empty"),
  workspace: document.getElementById("workspace"),
  groupTitle: document.getElementById("group-title"),
  groupMeta: document.getElementById("group-meta"),
  deleteGroup: document.getElementById("delete-group"),
  expenseForm: document.getElementById("expense-form"),
  payerSelect: document.getElementById("payer-select"),
  splitFields: document.getElementById("split-fields"),
  splitPreview: document.getElementById("split-preview"),
  expenseTable: document.getElementById("expense-table"),
  expenseEmpty: document.getElementById("expense-empty"),
  balanceList: document.getElementById("balance-list"),
  settlementList: document.getElementById("settlement-list"),
  settlementEmpty: document.getElementById("settlement-empty"),
  toast: document.getElementById("toast"),
};

/* -- helpers ------------------------------------------------------------ */

function esc(value) {
  return String(value).replace(/[&<>"']/g, (char) => {
    return {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[char];
  });
}

let toastTimer = null;
function toast(message, tone = "info") {
  els.toast.textContent = message;
  els.toast.dataset.tone = tone;
  els.toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    els.toast.hidden = true;
  }, 4000);
}

async function api(method, path, body) {
  const response = await fetch(path, {
    method,
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  const payload = text ? JSON.parse(text) : null;
  if (!response.ok) {
    const message = (payload && payload.error) || `Request failed (${response.status})`;
    throw new Error(message);
  }
  return payload;
}

function splitType() {
  const checked = els.expenseForm.querySelector('input[name="split-type"]:checked');
  return checked ? checked.value : "equal";
}

function members() {
  return state.group ? state.group.members : [];
}

/* -- groups ------------------------------------------------------------- */

async function loadGroups(preferredId) {
  const payload = await api("GET", "/api/groups");
  state.groups = payload.groups;
  renderGroups();
  const target =
    preferredId || (state.currentId && hasGroup(state.currentId) ? state.currentId : null);
  if (target) {
    await selectGroup(target);
  } else {
    showNothingSelected();
  }
}

function hasGroup(id) {
  return state.groups.some((group) => group.id === id);
}

function renderGroups() {
  els.groupEmpty.hidden = state.groups.length > 0;
  els.groupList.innerHTML = state.groups
    .map((group) => {
      const current = group.id === state.currentId ? "true" : "false";
      return `
        <li>
          <button type="button" data-group="${esc(group.id)}" aria-current="${current}">
            <span class="group-name">${esc(group.name)}</span>
            <span class="group-members">${esc(group.members.join(" \u00b7 "))}</span>
          </button>
        </li>`;
    })
    .join("");
}

function showNothingSelected() {
  state.currentId = null;
  state.group = null;
  state.expenses = [];
  state.settlement = null;
  els.workspace.hidden = true;
  els.workspaceEmpty.hidden = false;
  renderGroups();
}

async function selectGroup(id) {
  state.currentId = id;
  const [detail, expenseList, settlement] = await Promise.all([
    api("GET", `/api/groups/${encodeURIComponent(id)}`),
    api("GET", `/api/groups/${encodeURIComponent(id)}/expenses`),
    api("GET", `/api/groups/${encodeURIComponent(id)}/settlement`),
  ]);
  state.group = detail.group;
  state.expenses = expenseList.expenses;
  state.settlement = settlement;

  els.workspaceEmpty.hidden = true;
  els.workspace.hidden = false;
  els.groupTitle.textContent = state.group.name;
  els.groupMeta.textContent = `${members().length} members \u00b7 ${state.expenses.length} expenses \u00b7 total ${detail.total}`;

  els.payerSelect.innerHTML = members()
    .map((name) => `<option value="${esc(name)}">${esc(name)}</option>`)
    .join("");

  renderSplitFields();
  renderExpenses();
  renderSettlement();
  renderGroups();
}

/* -- split controls ----------------------------------------------------- */

function renderSplitFields() {
  const type = splitType();
  const people = members();
  if (type === "equal") {
    els.splitFields.innerHTML = people
      .map(
        (name) => `
        <label class="split__row">
          <span>${esc(name)}</span>
          <input type="checkbox" data-member="${esc(name)}" checked />
        </label>`
      )
      .join("");
  } else if (type === "exact") {
    els.splitFields.innerHTML = people
      .map(
        (name) => `
        <label class="split__row">
          <span>${esc(name)}</span>
          <input type="text" inputmode="decimal" data-member="${esc(name)}"
                 placeholder="0.00" value="" />
        </label>`
      )
      .join("");
  } else {
    els.splitFields.innerHTML = people
      .map(
        (name) => `
        <label class="split__row">
          <span>${esc(name)}</span>
          <input type="text" inputmode="decimal" data-member="${esc(name)}"
                 placeholder="1" value="1" />
        </label>`
      )
      .join("");
  }
  previewSplit();
}

function readSplitForm() {
  const type = splitType();
  const rows = [...els.splitFields.querySelectorAll("[data-member]")];
  if (type === "equal") {
    const participants = rows.filter((row) => row.checked).map((row) => row.dataset.member);
    return { type, participants };
  }
  const values = {};
  for (const row of rows) {
    if (row.value.trim() !== "") {
      values[row.dataset.member] = row.value.trim();
    }
  }
  return type === "exact" ? { type, shares: values } : { type, weights: values };
}

let previewToken = 0;
async function previewSplit() {
  const amountInput = els.expenseForm.elements.amount;
  const amount = amountInput.value.trim();
  const split = readSplitForm();
  if (!amount) {
    els.splitPreview.textContent = "";
    return;
  }
  const token = ++previewToken;
  try {
    const payload = await api("POST", "/api/split", { amount, split });
    if (token !== previewToken) return;
    const parts = Object.entries(payload.shares_display).map(
      ([name, value]) => `${name} ${value}`
    );
    els.splitPreview.textContent = `Each share: ${parts.join("  \u00b7  ")}`;
  } catch (error) {
    if (token === previewToken) {
      els.splitPreview.textContent = error.message;
    }
  }
}

/* -- rendering ---------------------------------------------------------- */

function renderExpenses() {
  const rows = state.expenses.map((expense) => {
    const shares = Object.entries(expense.shares)
      .map(([name, paise]) => `${esc(name)} ${(paise / 100).toFixed(2)}`)
      .join(" \u00b7 ");
    return `
      <tr>
        <td>
          ${esc(expense.description)}
          <span class="share-line">${shares}</span>
        </td>
        <td>${esc(expense.payer)}</td>
        <td class="num">${esc(expense.amount)}</td>
        <td>
          <button type="button" class="icon-btn" data-delete="${expense.id}"
                  title="Remove ${esc(expense.description)}"
                  aria-label="Remove ${esc(expense.description)}">&#215;</button>
        </td>
      </tr>`;
  });
  els.expenseTable.tBodies[0].innerHTML = rows.join("");
  els.expenseEmpty.hidden = state.expenses.length > 0;
}

function renderSettlement() {
  const settlement = state.settlement;
  if (!settlement) return;

  els.balanceList.innerHTML = Object.entries(settlement.balances)
    .map(([name, entry]) => {
      const tone = entry.paise > 0 ? "is-positive" : entry.paise < 0 ? "is-negative" : "";
      const label =
        entry.paise > 0
          ? `is owed ${entry.amount}`
          : entry.paise < 0
            ? `owes ${entry.amount.slice(1)}`
            : "settled";
      return `<li><span>${esc(name)}</span><span class="amount ${tone}">${esc(label)}</span></li>`;
    })
    .join("");

  els.settlementList.innerHTML = settlement.settlements
    .map(
      (transfer) => `
      <li>
        <span>${esc(transfer.from)} pays ${esc(transfer.to)}</span>
        <span class="amount">${esc(transfer.amount_display)}</span>
      </li>`
    )
    .join("");
  els.settlementEmpty.hidden = settlement.settlements.length > 0;
}

/* -- events ------------------------------------------------------------- */

els.groupForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const name = els.groupForm.elements.name.value.trim();
  const members = els.groupForm.elements.members.value
    .split(",")
    .map((member) => member.trim())
    .filter(Boolean);
  try {
    const payload = await api("POST", "/api/groups", { name, members });
    els.groupForm.reset();
    await loadGroups(payload.group.id);
    toast(`Created "${payload.group.name}"`);
  } catch (error) {
    toast(error.message, "error");
  }
});

els.groupList.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-group]");
  if (!button) return;
  try {
    await selectGroup(button.dataset.group);
  } catch (error) {
    toast(error.message, "error");
  }
});

els.deleteGroup.addEventListener("click", async () => {
  if (!state.currentId) return;
  const confirmed = window.confirm(`Delete "${state.group.name}" and all its expenses?`);
  if (!confirmed) return;
  try {
    await api("DELETE", `/api/groups/${encodeURIComponent(state.currentId)}`);
    toast("Group deleted");
    state.currentId = null;
    await loadGroups();
  } catch (error) {
    toast(error.message, "error");
  }
});

els.expenseForm
  .querySelectorAll('input[name="split-type"]')
  .forEach((radio) => radio.addEventListener("change", renderSplitFields));

els.splitFields.addEventListener("input", previewSplit);
els.splitFields.addEventListener("change", previewSplit);
els.expenseForm.elements.amount.addEventListener("input", previewSplit);

els.expenseForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.currentId) return;
  const form = els.expenseForm;
  const body = {
    description: form.elements.description.value.trim(),
    payer: form.elements.payer.value,
    amount: form.elements.amount.value.trim(),
    split: readSplitForm(),
  };
  try {
    await api("POST", `/api/groups/${encodeURIComponent(state.currentId)}/expenses`, body);
    form.elements.description.value = "";
    form.elements.amount.value = "";
    await selectGroup(state.currentId);
    toast("Expense added");
  } catch (error) {
    toast(error.message, "error");
  }
});

els.expenseTable.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-delete]");
  if (!button || !state.currentId) return;
  try {
    await api(
      "DELETE",
      `/api/groups/${encodeURIComponent(state.currentId)}/expenses/${button.dataset.delete}`
    );
    await selectGroup(state.currentId);
    toast("Expense removed");
  } catch (error) {
    toast(error.message, "error");
  }
});

/* -- boot --------------------------------------------------------------- */

loadGroups().catch((error) => toast(error.message, "error"));
