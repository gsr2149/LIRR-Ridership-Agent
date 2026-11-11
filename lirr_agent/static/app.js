/* LIRR Fare Desk: front end for the LIRR fare research agent.
 * Talks to POST /chat/stream (live NDJSON events, falls back to POST /chat),
 * GET /api/stations and GET /api/fare. No build step. */
"use strict";

const $ = (sel, el = document) => el.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (v) => (v == null ? "—" : "$" + Number(v).toFixed(2));

let sessionId = null;
let busy = false;

/* ---------------- clock on the board ---------------- */
function tick() {
  const t = new Date().toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "America/New_York" });
  $("#clock").textContent = t + " NYC";
}
tick();
setInterval(tick, 20000);

/* ---------------- commute check ---------------- */
const TICKET_LABELS = {
  monthly: "Monthly", weekly: "Weekly",
  one_way_peak: "One-way, peak", one_way_offpeak: "One-way, off-peak",
  one_way_senior_disabled: "One-way, senior/disabled",
  ten_trip_peak: "Ten-trip, peak", ten_trip_offpeak: "Ten-trip, off-peak",
  ten_trip_senior_disabled: "Ten-trip, senior/disabled",
  day_pass_weekday: "Day Pass, weekday", day_pass_weekend: "Day Pass, weekend",
  onboard_one_way_peak: "Bought onboard, peak", onboard_one_way_offpeak: "Bought onboard, off-peak",
};

fetch("/api/stations").then((r) => r.json()).then(({ stations }) => {
  $("#stations").innerHTML = stations.map((s) => `<option value="${esc(s.name)}">Zone ${esc(s.zone)}</option>`).join("");
}).catch(() => {});

$("#swap").addEventListener("click", () => {
  const a = $("#from").value;
  $("#from").value = $("#to").value;
  $("#to").value = a;
  if ($("#from").value && $("#to").value) $("#commute-form").requestSubmit();
});

$("#commute-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const from = $("#from").value.trim(), to = $("#to").value.trim();
  const out = $("#commute-result");
  out.innerHTML = `<p class="small">Looking up both fare charts…</p>`;
  try {
    const r = await fetch(`/api/fare?origin=${encodeURIComponent(from)}&destination=${encodeURIComponent(to)}`);
    const data = await r.json();
    if (data.error) { out.innerHTML = `<p class="error-box">${esc(data.error)}</p>`; return; }
    out.innerHTML = renderFares(data, data.origin_name || from, data.destination_name || to);
    $(".fare-ask button", out)?.addEventListener("click", (ev) => {
      $("#q").value = ev.currentTarget.dataset.q;
      $("#ask-form").requestSubmit();
      $("#ask-title").scrollIntoView({ behavior: "smooth" });
    });
  } catch {
    out.innerHTML = `<p class="error-box">Couldn't reach the fare lookup. Try again in a moment.</p>`;
  }
});

function renderFares(d, from, to) {
  const rows = d.fares.map((f) => {
    let change = "";
    if (f.change_pct != null) change = `<span class="pill ${f.change_pct > 0 ? "up" : "down"}">${f.change_pct > 0 ? "+" : ""}${f.change_pct}%</span>`;
    else if ((f.status || "").startsWith("discontinued")) change = `<span class="pill gone">retired</span>`;
    else if ((f.status || "").startsWith("new")) change = `<span class="pill new">new</span>`;
    else change = `<span class="small">no 2023 price</span>`;
    return `<tr><td>${esc(TICKET_LABELS[f.ticket_type] || f.ticket_type)}</td>
      <td class="num">${money(f.price_before_usd)}</td><td class="num">${money(f.price_after_usd)}</td>
      <td class="num">${change}</td></tr>`;
  }).join("");
  const notes = (d.notes || []).slice(1).map((n) => `<li>${esc(n)}</li>`).join("");
  const q = `How did ticket buying change for trips between ${from} and ${to} (zone ${d.origin_zone} and zone ${d.destination_zone}) after January 2026, and what do riders on that trip pay now?`;
  return `<div class="fare-head"><b>${esc(from)} ⇄ ${esc(to)}</b>
      <span class="zone-tag">ZONE ${esc(d.origin_zone)} → ${esc(d.destination_zone)}</span>
      <span class="small">Before = fares from Aug 20 2023 · Now = fares from Jan 4 2026</span></div>
    <div class="rows-wrap"><table class="fares"><thead><tr><th>Ticket</th><th class="num">Before</th><th class="num">Now</th><th class="num">Change</th></tr></thead>
    <tbody>${rows}</tbody></table></div>
    ${notes ? `<ul class="fare-notes small">${notes}</ul>` : ""}
    <div class="fare-ask"><button type="button" class="btn ghost" data-q="${esc(q)}">Ask the researchers what changed for riders on this trip →</button></div>`;
}

/* ---------------- asking ---------------- */
$("#examples").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-q]");
  if (!b || busy) return;
  $("#q").value = b.dataset.q;
  $("#ask-form").requestSubmit();
});
$("#q").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#ask-form").requestSubmit(); }
});
$("#new-chat").addEventListener("click", () => {
  if (busy) return;
  sessionId = null;
  $("#thread").innerHTML = "";
  $("#new-chat").hidden = true;
  $("#q").focus();
});
$("#ask-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const message = $("#q").value.trim();
  if (!message || busy) return;
  $("#q").value = "";
  ask(message);
});

async function ask(message) {
  busy = true;
  $("#ask-btn").disabled = true;
  const turn = $("#turn-tpl").content.firstElementChild.cloneNode(true);
  $(".you", turn).textContent = message;
  $("#thread").append(turn);
  turn.scrollIntoView({ behavior: "smooth", block: "start" });
  const live = new LiveBoard(turn);

  try {
    const body = await streamChat(message, (ev) => live.onEvent(ev));
    sessionId = body.session_id;
    live.finish();
    renderAnswer(turn, body);
  } catch (err) {
    live.finish();
    const a = $(".answer", turn);
    a.hidden = false;
    a.innerHTML = `<p class="error-box">${esc(err.message || "Something went wrong.")}</p>`;
  } finally {
    busy = false;
    $("#ask-btn").disabled = false;
    $("#new-chat").hidden = false;
  }
}

/* Stream /chat/stream; if streaming fails before it starts, use plain /chat. */
async function streamChat(message, onEvent) {
  const payload = JSON.stringify({ message, session_id: sessionId });
  let res;
  try {
    res = await fetch("/chat/stream", { method: "POST", headers: { "Content-Type": "application/json" }, body: payload });
  } catch { res = null; }
  if (!res || !res.ok || !res.body) {
    const r = await fetch("/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: payload });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || `The agent returned an error (${r.status}).`);
    return data;
  }
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let nl;
    while ((nl = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, nl).trim();
      buf = buf.slice(nl + 1);
      if (!line) continue;
      const ev = JSON.parse(line);
      if (ev.type === "done") return ev;
      if (ev.type === "error") throw new Error(ev.detail);
      onEvent(ev);
    }
  }
  throw new Error("The connection closed before the answer arrived. Please try again.");
}

/* ---------------- live research board ---------------- */
const PHASES = ["planning", "researching", "reviewing", "writing"];
const PHASE_TEXT = {
  planning: "Lead is reading your question and planning the research…",
  researching: "Researchers are querying the data in parallel…",
  reviewing: "Lead is checking the findings for gaps…",
  writing: "Writer is drafting a cited answer; code is checking every number…",
};

class LiveBoard {
  constructor(turn) {
    this.el = $(".live", turn);
    this.lanes = $(".lanes", turn);
    this.started = Date.now();
    this.researchers = {};
    this.timer = setInterval(() => { $(".elapsed", this.el).textContent = Math.round((Date.now() - this.started) / 1000) + "s"; }, 500);
    this.lead = this.lane("lead", "Lead", "planning");
    this.grid = document.createElement("div");
    this.grid.className = "researchers";
    this.lanes.append(this.grid);
  }
  lane(kind, name, state) {
    const div = document.createElement("div");
    div.className = `lane ${kind}`;
    div.innerHTML = `<div class="lane-head"><span>${esc(name)}</span><span class="state">${esc(state)}</span></div><div class="lane-body"></div>`;
    (kind === "researcher" ? this.grid : this.lanes).append(div);
    return div;
  }
  setPhase(phase) {
    const i = PHASES.indexOf(phase);
    this.el.querySelectorAll(".rail li").forEach((li, j) => {
      li.classList.toggle("done", j < i);
      li.classList.toggle("now", j === i);
    });
    $(".status-text", this.el).textContent = PHASE_TEXT[phase] || "";
  }
  onEvent(ev) {
    if (ev.type === "phase") this.setPhase(ev.phase);
    else if (ev.type === "researcher_start") this.researcher(ev.agent, ev.sub_question);
    else if (ev.type === "step") this.step(ev.entry);
  }
  researcher(agent, subq) {
    if (this.researchers[agent]) return this.researchers[agent];
    const n = agent.replace("researcher-", "");
    const lane = this.lane("researcher", `Researcher ${n}`, "querying…");
    $(".lane-body", lane).innerHTML = `<p class="subq">${esc(subq || "")}</p><ul class="steps"></ul>`;
    this.researchers[agent] = lane;
    return lane;
  }
  step(e) {
    if (e.agent === "lead") return this.leadStep(e);
    if (e.agent === "writer") {
      this.writer = this.writer || this.lane("writer", "Writer", "checking");
      const body = $(".lane-body", this.writer);
      if (e.name === "verify_citations") body.insertAdjacentHTML("beforeend", `<p class="summary">Citations checked: ${e.result.cited.length} queries cited${e.result.invalid_removed.length ? `, ${e.result.invalid_removed.length} invalid removed` : ""}.</p>`);
      if (e.name === "verify_numbers") body.insertAdjacentHTML("beforeend", `<p class="summary">Numbers checked against query rows: ${e.result.grounded} of ${e.result.checked}.</p>`);
      return;
    }
    const lane = this.researcher(e.agent, e.args?.sub_question);
    const steps = $(".steps", lane);
    if (e.name === "report_finding") {
      $(".state", lane).textContent = "reported ✓";
      const text = (e.result.summary || "").replace(/^FINDING:\s*/i, "").split(/\nLIMITS:/i)[0]
        .replace(/\[[^\]]*\]/g, "").replace(/[#*_`>]+/g, "").replace(/\s+/g, " ").trim();
      $(".lane-body", lane).insertAdjacentHTML("beforeend", `<p class="summary">${esc(text.slice(0, 260))}${text.length > 260 ? "…" : ""}</p>`);
      return;
    }
    const r = e.result || {};
    let tag = e.name, what = "";
    if (e.name === "run_sql") { tag = "SQL"; what = r.error ? `error: ${r.error}` : `${r.purpose || "query"} · ${r.row_count} rows · ${r.elapsed_ms} ms`; }
    else if (e.name === "lookup_fare") { tag = "FARE"; what = r.error || `${r.origin} → ${r.destination}`; }
    else if (e.name === "describe_table") { tag = "SCHEMA"; what = e.args?.table || ""; }
    steps.insertAdjacentHTML("beforeend", `<li class="${r.error ? "err" : ""}"><span class="tag">${esc(tag)}</span><span class="what" title="${esc(what)}">${esc(what)}</span></li>`);
  }
  leadStep(e) {
    const body = $(".lane-body", this.lead);
    if (e.name === "plan") {
      const r = e.result;
      $(".state", this.lead).textContent = r.action;
      if (r.action === "research") body.innerHTML = `<p class="summary">Split the question into ${r.sub_questions.length} angles:</p><ol class="plan-list">${r.sub_questions.map((q) => `<li>${esc(q)}</li>`).join("")}</ol>`;
      else body.innerHTML = `<p class="summary">${r.action === "clarify" ? "The question needs one more detail." : "The data can't answer this one."}</p>`;
    } else if (e.name === "review") {
      const r = e.result;
      $(".state", this.lead).textContent = r.done ? "satisfied" : "sending follow-ups";
      body.insertAdjacentHTML("beforeend", `<p class="summary"><b>Review ${esc(e.args.round)}:</b> ${esc(r.reason || "")}${r.follow_up_questions?.length ? ` → ${r.follow_up_questions.map(esc).join(" · ")}` : ""}</p>`);
    }
  }
  finish() {
    clearInterval(this.timer);
    this.el.classList.add("finished");
    this.el.querySelectorAll(".rail li").forEach((li) => { li.classList.remove("now"); });
    const secs = Math.round((Date.now() - this.started) / 1000);
    $(".status-text", this.el).textContent = `Done in ${secs}s.`;
    $(".elapsed", this.el).textContent = "";
    const nR = Object.keys(this.researchers).length;
    const nQ = this.el.querySelectorAll(".steps li").length;
    $(".live-summary", this.el).textContent = `Research log · ${nR} researcher${nR === 1 ? "" : "s"} · ${nQ} tool call${nQ === 1 ? "" : "s"} · ${secs}s`;
    this.el.open = false;
  }
}

/* ---------------- answer ---------------- */
const CITE_RE = /\[((?:q_[0-9a-f]{8}|fare chart \d{4}-\d{2}-\d{2}|MTA announcement)(?:\s*,\s*(?:q_[0-9a-f]{8}|fare chart \d{4}-\d{2}-\d{2}|MTA announcement))*)\]/g;

function citeLinks(md, turnId) {
  return md.replace(CITE_RE, (_, inner) => inner.split(/\s*,\s*/).map((t) => {
    if (t.startsWith("q_")) return `<a class="cite" href="#${turnId}-${t}" data-q="${t}">${t}</a>`;
    if (t.startsWith("fare chart")) return `<a class="cite fare" href="#commute-title" title="LIRR fare chart effective ${t.slice(11)}">${t.replace("fare chart ", "fares ")}</a>`;
    return `<a class="cite bg" title="Background from MTA announcements, not from the datasets">MTA</a>`;
  }).join(" "));
}

let turnCounter = 0;
function renderAnswer(turn, body) {
  const turnId = `t${++turnCounter}`;
  const calls = body.tool_calls || [];
  const plan = calls.find((c) => c.agent === "lead" && c.name === "plan");
  const kind = plan?.result?.action;
  const a = $(".answer", turn);
  a.hidden = false;
  if (kind === "clarify") a.classList.add("clarify");
  if (kind === "out_of_scope") a.classList.add("scope");
  if (kind !== "research") $(".live", turn).hidden = true;

  const options = (kind === "clarify" || kind === "out_of_scope") ? (plan.result.options || []) : [];
  let text = body.response || "";
  if (options.length) text = text.split(/\n\nYou could ask:\n/)[0];  // shown as buttons instead
  const html = DOMPurify.sanitize(marked.parse(citeLinks(text, turnId)));
  const numbers = calls.find((c) => c.name === "verify_numbers")?.result;
  const cites = calls.find((c) => c.name === "verify_citations")?.result;
  const charts = calls.filter((c) => c.name === "chart").map((c) => c.result);

  a.innerHTML = `
    ${kind === "clarify" ? `<p class="answer-kind">One question first — reply below</p>` : ""}
    ${kind === "out_of_scope" ? `<p class="answer-kind">Outside what this data can answer</p>` : ""}
    <div class="answer-body">${html}</div>
    ${options.length ? `<div class="options">${options.map((o) => `<button type="button" data-q="${esc(o)}">${esc(o)}</button>`).join("")}</div>` : ""}
    ${checksHtml(cites, numbers)}
    <div class="charts"></div>
    ${kind === "research" ? traceHtml(calls, turnId) : ""}`;

  charts.forEach((spec) => drawChart($(".charts", a), spec, turnId));
  a.addEventListener("click", (e) => {
    const opt = e.target.closest(".options button[data-q]");
    if (opt && !busy) { $("#q").value = opt.dataset.q; $("#ask-form").requestSubmit(); return; }
    const link = e.target.closest("a.cite[data-q]");
    if (!link) return;
    e.preventDefault();
    const card = document.getElementById(`${turnId}-${link.dataset.q}`);
    if (!card) return;
    card.closest("details").open = true;
    card.scrollIntoView({ behavior: "smooth", block: "center" });
    card.classList.remove("flash"); void card.offsetWidth; card.classList.add("flash");
  });
  if (kind === "clarify") $("#q").focus();
}

function checksHtml(cites, numbers) {
  if (!cites && !numbers) return "";
  const out = [];
  if (cites) out.push(`<span class="badge ${cites.invalid_removed.length ? "warn" : "ok"}">✓ ${cites.cited.length} cited quer${cites.cited.length === 1 ? "y" : "ies"} verified as run${cites.invalid_removed.length ? ` · ${cites.invalid_removed.length} bad citation removed` : ""}</span>`);
  if (numbers && numbers.checked) {
    const ok = numbers.grounded === numbers.checked;
    out.push(`<span class="badge ${ok ? "ok" : "warn"}" title="Code checks that each number next to a citation appears in that query's rows, or is a simple difference, ratio or percent of them.">${ok ? "✓" : "!"} ${numbers.grounded} of ${numbers.checked} numbers match the query rows</span>`);
  }
  return `<div class="checks">${out.join("")}</div>`;
}

const HUMAN = { sold_as: "ticket type", device_class_type: "sales channel", fare_type: "fare type",
  number_of_tickets: "tickets", pct: "%", n: "count" };
function human(s) {
  return String(s ?? "").split("_").map((w) => HUMAN[w] ?? w).join(" ").replace(/\b(\w+) %/g, "$1 (%)");
}

function cssVar(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }

function drawChart(container, spec, turnId) {
  const box = document.createElement("figure");
  box.className = "chart";
  box.innerHTML = `<h4>${esc(spec.title)} <a class="cite" href="#${turnId}-${spec.query_id}" data-q="${spec.query_id}">${spec.query_id}</a></h4><div class="box"><canvas role="img" aria-label="${esc(spec.title)}"></canvas></div>`;
  container.append(box);
  const colors = ["--c1", "--c2", "--c3", "--c4", "--c5", "--c6"].map(cssVar);
  const ink = cssVar("--muted"), grid = cssVar("--line");
  new Chart($("canvas", box), {
    type: spec.type,
    data: {
      labels: spec.labels,
      datasets: spec.series.map((s, i) => ({
        label: human(s.name), data: s.values, borderColor: colors[i % 6], backgroundColor: colors[i % 6],
        borderWidth: 2, pointRadius: spec.labels.length > 24 ? 0 : 2.5, tension: 0.25, spanGaps: true,
      })),
    },
    options: {
      maintainAspectRatio: false, animation: { duration: 500 },
      plugins: { legend: { display: spec.series.length > 1, labels: { color: ink, boxWidth: 12, font: { family: "Overpass" } } } },
      scales: {
        x: { ticks: { color: ink, maxRotation: 45, autoSkip: true, font: { family: "Overpass Mono", size: 10 } }, grid: { color: grid } },
        y: { ticks: { color: ink, font: { family: "Overpass Mono", size: 10 } }, grid: { color: grid },
             title: { display: !!spec.y_label, text: human(spec.y_label), color: ink } },
      },
    },
  });
}

/* ---------------- trace ---------------- */
function rowsTable(cols, rows, max = 12) {
  if (!cols?.length) return "";
  const head = cols.map((c) => `<th>${esc(c)}</th>`).join("");
  const body = rows.slice(0, max).map((r) => `<tr>${r.map((v) => `<td>${esc(typeof v === "number" ? v.toLocaleString("en-US", { maximumFractionDigits: 2 }) : v)}</td>`).join("")}</tr>`).join("");
  const more = rows.length > max ? `<p class="small">…and ${rows.length - max} more rows</p>` : "";
  return `<div class="rows-wrap"><table class="rows"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>${more}`;
}

function traceHtml(calls, turnId) {
  const groups = new Map();
  calls.forEach((c) => { if (!groups.has(c.agent)) groups.set(c.agent, []); groups.get(c.agent).push(c); });
  const nSql = calls.filter((c) => c.name === "run_sql").length;
  const sections = [...groups].map(([agent, items]) => {
    const parts = items.map((c) => {
      const r = c.result || {};
      if (c.name === "run_sql") {
        return `<div class="qcard" id="${turnId}-${esc(r.query_id)}"><div class="qcard-head"><code>${esc(r.query_id)}</code><span>${esc(r.purpose || "")}</span>
          <span class="small">${r.error ? "" : `${r.row_count} rows · ${r.elapsed_ms} ms`}</span></div>
          <pre>${esc(r.sql || c.args.sql)}</pre>${r.error ? `<p class="error-box">${esc(r.error)}</p>` : rowsTable(r.columns, r.rows)}</div>`;
      }
      if (c.name === "lookup_fare") {
        const rows = (r.fares || []).map((f) => [TICKET_LABELS[f.ticket_type] || f.ticket_type, money(f.price_before_usd), money(f.price_after_usd), f.change_pct != null ? f.change_pct + "%" : (f.status || "")]);
        return `<div class="qcard"><div class="qcard-head"><code>lookup_fare</code><span>${esc(r.origin || c.args.origin)} → ${esc(r.destination || c.args.destination)}</span></div>${r.error ? `<p class="error-box">${esc(r.error)}</p>` : rowsTable(["ticket", "before", "after", "change"], rows)}</div>`;
      }
      if (c.name === "report_finding") return `<div class="qcard"><div class="qcard-head"><b>Finding</b> <span class="small">${esc(c.args.sub_question)}</span></div><pre>${esc(r.summary)}</pre></div>`;
      if (c.name === "plan" || c.name === "review" || c.name === "dispatch_researchers" || c.name.startsWith("verify")) {
        return `<div class="qcard"><div class="qcard-head"><code>${esc(c.name)}</code></div><pre>${esc(JSON.stringify(c.result, null, 1))}</pre></div>`;
      }
      if (c.name === "chart") return "";
      return `<div class="qcard"><div class="qcard-head"><code>${esc(c.name)}</code><span class="small">${esc(JSON.stringify(c.args))}</span></div></div>`;
    }).join("");
    return `<div class="trace-agent"><h4>${esc(agent)}</h4>${parts}</div>`;
  }).join("");
  return `<details class="trace"><summary>Show the work · ${nSql} SQL quer${nSql === 1 ? "y" : "ies"} across ${groups.size} agents</summary><div class="trace-body">${sections}</div></details>`;
}
