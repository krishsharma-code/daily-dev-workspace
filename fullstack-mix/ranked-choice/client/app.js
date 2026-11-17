/* Ranked Choice client — vanilla JS, no build step. */
"use strict";

const form = document.getElementById("rcv-form");
const ballotsInput = document.getElementById("ballots");
const candidatesInput = document.getElementById("candidates");
const output = document.getElementById("output");
const errorBox = document.getElementById("error");
const status = document.getElementById("status");
const countButton = document.getElementById("count-button");
const exampleButton = document.getElementById("load-example");
const clearButton = document.getElementById("clear-ballots");

const REASON_TEXT = {
  majority: "reached a majority of the votes still counting",
  last_standing: "was the last candidate left in the count",
  tie: "could not be separated from the rest of the field",
};

/* ------------------------------------------------------------------ */
/* Small DOM helpers                                                   */
/* ------------------------------------------------------------------ */

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

function showError(message) {
  errorBox.textContent = message;
  errorBox.hidden = false;
}

function hideError() {
  errorBox.hidden = true;
  errorBox.textContent = "";
}

/* ------------------------------------------------------------------ */
/* Request                                                             */
/* ------------------------------------------------------------------ */

function payload() {
  const body = { ballots: ballotsInput.value };
  const candidates = candidatesInput.value.trim();
  if (candidates) body.candidates = candidates;
  return body;
}

async function count() {
  hideError();
  countButton.disabled = true;
  status.textContent = "Counting…";
  try {
    const response = await fetch("/api/tabulate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload()),
    });
    let data;
    try {
      data = await response.json();
    } catch (err) {
      showError("The server sent something that is not JSON.");
      return;
    }
    if (!response.ok) {
      showError(data && data.error ? data.error : "The count failed.");
      return;
    }
    render(data);
    status.textContent = `Counted ${data.ballots.weight} vote${
      data.ballots.weight === 1 ? "" : "s"
    } in ${data.rounds.length} round${data.rounds.length === 1 ? "" : "s"}.`;
  } catch (err) {
    showError("Could not reach the server. Is `python server/app.py` running?");
    status.textContent = "";
  } finally {
    countButton.disabled = false;
  }
}

async function loadExample() {
  try {
    const response = await fetch("/api/example");
    const data = await response.json();
    ballotsInput.value = data.request.ballots;
    candidatesInput.value = (data.request.candidates || []).join(", ");
    hideError();
    status.textContent = "Example loaded — press Count the vote.";
  } catch (err) {
    showError("Could not fetch the example from the server.");
  }
}

/* ------------------------------------------------------------------ */
/* Rendering                                                           */
/* ------------------------------------------------------------------ */

function verdictCard(report) {
  const card = el("section", "verdict");
  if (report.winner) {
    card.classList.add("win");
    card.appendChild(el("h2", null, "Winner"));
    card.appendChild(el("p", "name", report.winner));
    card.appendChild(
      el("p", "detail", `${report.winner} ${REASON_TEXT[report.reason] || "won the count"}.`)
    );
    return card;
  }

  card.classList.add("draw");
  card.appendChild(el("h2", null, "No winner"));
  card.appendChild(el("p", "name", (report.tied || []).join(" / ") || "Dead heat"));
  card.appendChild(
    el(
      "p",
      "detail",
      "Every candidate left in the count had the same number of votes, so nothing could be eliminated."
    )
  );
  return card;
}

function chips(report) {
  const row = el("div", "chips");
  const items = [
    ["Ballots", report.ballots.lines],
    ["Votes counted", report.ballots.weight],
    ["Distinct rankings", report.ballots.distinct_rankings],
    ["Rounds", report.rounds.length],
    ["Head-to-head winner", report.condorcet || "none"],
  ];
  for (const [label, value] of items) {
    const chip = el("span", "chip", `${label}: `);
    chip.appendChild(el("strong", null, value));
    row.appendChild(chip);
  }
  return row;
}

function tallyTable(record) {
  const table = el("table");
  const head = el("thead");
  const headRow = el("tr");
  for (const [label, cls] of [
    ["Candidate", null],
    ["Votes", "num"],
    ["Share", null],
    ["Status", null],
  ]) {
    headRow.appendChild(el("th", cls, label));
  }
  head.appendChild(headRow);
  table.appendChild(head);

  const body = el("tbody");
  const names = Object.keys(record.tally);
  names.sort((a, b) => record.tally[b] - record.tally[a] || a.localeCompare(b));

  for (const name of names) {
    const votes = record.tally[name];
    const share = record.active_votes ? (votes / record.active_votes) * 100 : 0;
    const row = el("tr");

    if (record.winner === name) row.classList.add("won");
    else if (record.eliminated === name) row.classList.add("out");

    row.appendChild(el("td", null, name));

    const votesCell = el("td", "num", votes);
    row.appendChild(votesCell);

    const shareCell = el("td");
    const bar = el("div", "bar");
    const fill = el("span");
    fill.style.width = `${share.toFixed(1)}%`;
    bar.appendChild(fill);
    shareCell.appendChild(bar);
    row.appendChild(shareCell);

    const statusCell = el("td");
    if (record.winner === name) {
      statusCell.appendChild(el("span", "tag won", "Elected"));
    } else if (record.eliminated === name) {
      statusCell.appendChild(el("span", "tag out", "Eliminated"));
    } else if (record.tie_break.includes(name)) {
      statusCell.appendChild(el("span", "tag tied", "Tied for last"));
    } else {
      statusCell.appendChild(el("span", "tag", "Continuing"));
    }
    row.appendChild(statusCell);

    body.appendChild(row);
  }
  table.appendChild(body);
  return table;
}

function roundCard(record) {
  const card = el("section", "card");
  card.appendChild(el("h3", null, `Round ${record.round}`));

  const parts = [
    `${record.active_votes} vote${record.active_votes === 1 ? "" : "s"} counting`,
    `${record.majority_needed} needed to win`,
    `${record.exhausted} exhausted`,
  ];
  if (record.eliminated) parts.push(`eliminated ${record.eliminated}`);
  const meta = el("p", "round-meta");
  for (const part of parts) meta.appendChild(el("span", null, part));
  card.appendChild(meta);

  card.appendChild(tallyTable(record));

  if (record.eliminated && record.tie_break.length > 1) {
    card.appendChild(
      el(
        "p",
        "footnote",
        `Tied for last: ${record.tie_break.join(", ")}. The count eliminates the one with fewest first-choice votes, then the first alphabetically — here, ${record.eliminated}.`
      )
    );
  }
  return card;
}

function pairwiseCard(report) {
  const card = el("section", "card");
  card.appendChild(el("h3", null, "Head to head"));
  card.appendChild(
    el(
      "p",
      "sub",
      "Each cell is the number of votes preferring the row candidate over the column candidate."
    )
  );

  const names = report.candidates;
  const table = el("table", "matrix");
  const head = el("thead");
  const headRow = el("tr");
  headRow.appendChild(el("th", null, "prefers"));
  for (const name of names) headRow.appendChild(el("th", "num", name));
  head.appendChild(headRow);
  table.appendChild(head);

  const body = el("tbody");
  for (const row of names) {
    const tr = el("tr");
    tr.appendChild(el("th", null, row));
    for (const column of names) {
      if (row === column) {
        tr.appendChild(el("td", "num", "—"));
        continue;
      }
      const value = report.pairwise[row][column];
      const cell = el("td", "num", value);
      if (value > report.pairwise[column][row]) cell.classList.add("winner-cell");
      tr.appendChild(cell);
    }
    body.appendChild(tr);
  }
  table.appendChild(body);
  card.appendChild(table);

  if (report.condorcet) {
    const agrees = report.winner === report.condorcet;
    card.appendChild(
      el(
        "p",
        "footnote",
        agrees
          ? `${report.condorcet} beats every other candidate head-to-head, and the runoff elected them too.`
          : `${report.condorcet} beats every other candidate head-to-head, yet the runoff elected ${report.winner}. That disagreement is a known property of instant-runoff voting, not a counting error.`
      )
    );
  } else {
    card.appendChild(
      el(
        "p",
        "footnote",
        "No Condorcet winner: the head-to-head results are tied or cyclic, so nobody beats every other candidate."
      )
    );
  }
  return card;
}

function render(report) {
  clear(output);
  output.appendChild(verdictCard(report));
  output.appendChild(chips(report));
  for (const record of report.rounds) output.appendChild(roundCard(record));
  output.appendChild(pairwiseCard(report));
}

/* ------------------------------------------------------------------ */
/* Wiring                                                              */
/* ------------------------------------------------------------------ */

form.addEventListener("submit", (event) => {
  event.preventDefault();
  count();
});

exampleButton.addEventListener("click", loadExample);

clearButton.addEventListener("click", () => {
  ballotsInput.value = "";
  candidatesInput.value = "";
  hideError();
  status.textContent = "";
  ballotsInput.focus();
});
