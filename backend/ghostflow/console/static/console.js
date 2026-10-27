// GhostFlow Demo Console. Read-only: it only GETs /api/demo/* and renders with textContent
// (never HTML parsing), so nothing in an evidence file can become markup or code.
// Everything shown is derived from the evidence or the backend; nothing is invented.
"use strict";

const API = "/api/demo";
const LIVE_POLL_MS = 900;
const SOURCES_POLL_MS = 3000;
const REPLAY_STEP_MS = 650;

const CHECK_LABELS = {
  candidates_available: "Eligible candidates on the page",
  candidate_exists: "Proposed id is one GhostFlow extracted",
  confidence: "Confidence meets the policy",
  action_compatible: "Element kind fits the action",
  role_compatible: "Accessible role is compatible",
  visible_enabled: "Visible and enabled",
  unambiguous: "Unique on the page",
  allowed_destination: "Destination inside allowed origins",
  expected_destination: "Leads where the workflow goes next",
  side_effects: "No side effects (submit, download, new tab)",
  live_recheck: "Re-checked live right before acting",
};
const STATE_TEXT = {
  pending: "Pending", running: "Running", completed: "Completed", failed: "Failed",
  recovered: "Recovered", skipped: "Not run",
};
const ACTIONS = {
  navigate: ["🌐", "Open page"], wait_for_url: ["⏳", "Wait for page"], click: ["👆", "Click"],
  fill: ["⌨️", "Type into"], select: ["☰", "Choose in"], check: ["☑️", "Check"], press: ["⏎", "Press key on"],
};
const MOOD_TEXT = {
  idle: "waiting", working: "working on a step", healing: "looking for an equivalent element",
  alert: "stopped safely", happy: "workflow completed", proud: "recovered and completed",
};
const PAGE_ACTIONS = new Set(["navigate", "wait_for_url"]);
const STEP_MARK = { completed: "✓", failed: "✕", recovered: "★", running: "•" };

const state = {
  sources: [],
  backend: null,
  backendOnline: null,
  current: null, // "collection/name"
  detail: null,
  scenarioKey: null,
  userPickedScenario: false,
  pinned: null, // step index the user selected
  view: new URLSearchParams(location.search).get("view") === "developer" ? "developer" : "demo",
  replay: null, // {key, index}
  error: null,
  timers: { live: null, replay: null },
};

// -- tiny DOM helpers --------------------------------------------------------------------

function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else node.setAttribute(k, v === true ? "" : String(v));
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}
const $ = (id) => document.getElementById(id);
const fill = (node, ...children) => { node.replaceChildren(); node.append(...children.flat().filter((c) => c !== null && c !== undefined && c !== false)); return node; };
const badge = (kind, text, dot) => el("span", { class: `badge ${kind}` }, dot ? el("span", { class: "dot", "aria-hidden": "true" }) : null, text);

async function getJSON(path) {
  const res = await fetch(path, { cache: "no-store", headers: { accept: "application/json" } });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

// -- data helpers ------------------------------------------------------------------------

const keyOf = (s) => `${s.collection}/${s.name}`;
const bundle = () => state.detail && state.detail.bundle;
const scenario = () => {
  const b = bundle();
  return b ? b.scenarios.find((s) => s.key === state.scenarioKey) || b.scenarios[0] || null : null;
};
const recoveredSteps = (sc) => sc.steps.filter((s) => s.state === "recovered");
const healingStep = (sc) => sc.steps.find((s) => s.recovery || s.phase === "recovering") || null;
const replaying = (sc) => !!(state.replay && sc && state.replay.key === sc.key);
const replayDone = (sc) => !replaying(sc);
const isLive = () => !!(state.detail && state.detail.presentation.mode === "live");
const utc = (iso) => (iso ? `${iso.replace("T", " ").slice(0, 16)} UTC` : "—");
const fmtMs = (ms) => (ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`);
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
const cleanTitle = (sc) => sc.title.replace(/^[A-Z]\d* · /, "");

function elementName(description) {
  const m = /"([^"]+)"/.exec(description || "");
  return m ? m[1] : description || "element";
}

// Input values are matched to fields by their label, the way the workflow named its parameters.
const slug = (t) => (t || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
function inputValue(sc, name) {
  const v = (sc.inputs || {})[slug(name)];
  return v === null || v === undefined || v === "" ? null : String(v);
}

function scenarioOutcome(sc) {
  if (replaying(sc)) return { cls: "running", text: "Replaying saved evidence…" };
  if (sc.state === "pending") return { cls: "pending", text: "Pending" };
  if (sc.state === "running") return { cls: "running", text: "Running…" };
  if (sc.state === "interrupted") return { cls: "error", text: "Interrupted" };
  if (sc.status === "passed" && recoveredSteps(sc).length) return { cls: "recovered", text: `Recovered ${recoveredSteps(sc).map((s) => s.id).join(", ")} · completed` };
  if (sc.status === "passed") return { cls: "passed", text: "Completed" };
  const failed = sc.steps.find((s) => s.state === "failed");
  if (sc.status === "failed") return { cls: "failed", text: failed ? `Failed at ${failed.id}` : "Failed" };
  return { cls: "error", text: "Error" };
}

// Replay rewrites step states up to a cursor; live and saved views use the stored ones.
function visibleSteps(sc) {
  if (!replaying(sc)) return sc.steps;
  const cursor = state.replay.index;
  return sc.steps.map((s, i) => {
    if (i < cursor) return s;
    if (i === cursor) return { ...s, state: s.state === "skipped" ? "skipped" : "running", phase: s.recovery && s.recovery.consulted_model ? "recovering" : null, error: null };
    return { ...s, state: "pending", phase: null, error: null };
  });
}

function focusIndex(sc, steps) {
  if (replaying(sc)) return Math.min(state.replay.index, steps.length - 1);
  if (state.pinned !== null && state.pinned < steps.length) return state.pinned;
  const find = (pred) => steps.findIndex(pred);
  for (const i of [find((s) => s.state === "running"), find((s) => s.state === "failed"), find((s) => s.state === "recovered")]) {
    if (i >= 0) return i;
  }
  let last = 0;
  steps.forEach((s, i) => { if (s.state === "completed") last = i; });
  return last;
}

function whereOf(s) {
  if (s.location) return s.location;
  const m = /^(?:Open|Wait for) (\S+)$/.exec(s.label || ""); // bundles written before `location` existed
  return m ? m[1] : null;
}
function pathOf(where) {
  try { return new URL(where).pathname; } catch (e) { return where || ""; }
}
function pageName(where) {
  const leaf = pathOf(where).split("/").filter(Boolean).pop() || "";
  const base = leaf.replace(/\.[a-z0-9]+$/i, "").replace(/[-_]+/g, " ").trim();
  if (base.toLowerCase() === "index") return "Home";
  return base ? base.charAt(0).toUpperCase() + base.slice(1) : "Start page";
}

// Pages come from the workflow itself: every navigate / wait_for_url step starts a new one.
function pageGroups(steps) {
  const groups = [];
  steps.forEach((s, i) => {
    if (!groups.length || PAGE_ACTIONS.has(s.action)) groups.push({ where: whereOf(s), idx: [] });
    groups[groups.length - 1].idx.push(i);
  });
  return groups.map((g) => {
    const ss = g.idx.map((i) => steps[i]);
    const has = (st) => ss.some((s) => s.state === st);
    const st = has("failed") ? "failed" : has("running") ? "running" : has("recovered") ? "recovered"
      : ss.every((s) => s.state === "completed") ? "completed" : ss.every((s) => s.state === "skipped") ? "skipped"
        : has("completed") ? "running" : "pending";
    const timed = ss.filter((s) => s.duration_ms !== null && s.duration_ms !== undefined && s.state !== "pending");
    return { ...g, steps: ss, state: st, ms: timed.length ? timed.reduce((t, s) => t + s.duration_ms, 0) : null, name: pageName(g.where), path: pathOf(g.where) };
  });
}

// The address bar follows the workflow's own navigate/wait_for_url steps (derived, not captured).
function locationAt(steps, idx, b) {
  let origin = (b.workflow.allowed_origins || [])[0] || "";
  let loc = origin;
  steps.slice(0, idx + 1).forEach((s, i) => {
    const where = whereOf(s);
    if (!where) return;
    if (s.action === "navigate" && (s.state !== "pending" || i === idx)) {
      loc = where;
      try { origin = new URL(where).origin; } catch (e) { /* keep the allowed origin */ }
    } else if (s.action === "wait_for_url" && s.state === "completed") {
      loc = origin + where;
    }
  });
  return loc;
}

function moodFor(sc, step) {
  if (!step) return "idle";
  if (step.phase === "recovering") return "healing";
  if (step.state === "running") return "working";
  if (step.state === "failed" || sc.status === "error") return "alert";
  if (sc.state === "finished" && replayDone(sc) && sc.status === "passed" && state.pinned === null) return recoveredSteps(sc).length ? "proud" : "happy";
  if (step.state === "recovered") return "proud";
  return "idle";
}

function stepText(s) {
  const verb = (ACTIONS[s.action] || ["", s.action])[1];
  if (s.action === "navigate") return `Open ${pathOf(whereOf(s)) || "page"}`;
  if (s.action === "wait_for_url") return `Reach ${whereOf(s) || "page"}`;
  if (s.state === "recovered" && s.recovery && s.recovery.proposed) return `${verb} ${s.recovery.proposed.name || s.recovery.proposed.id}`;
  return `${verb} ${elementName(s.target || s.label)}`;
}

function pin(i) {
  state.pinned = state.pinned === i ? null : i;
  render();
}

// -- header, sidebar, badges ------------------------------------------------------------------

function renderSystem() {
  const b = state.backend;
  const hint = "How the backend is configured right now. The evidence on screen may come from another run; its own labels say which.";
  const label = el("span", { class: "sys-label" }, "Current backend settings");
  if (state.backendOnline === false) return fill($("system-status"), label, el("span", { class: "pill fail" }, el("span", { class: "dot", "aria-hidden": "true" }), "Backend unreachable"));
  if (!b) return fill($("system-status"));
  fill($("system-status"), label,
    el("span", { class: "pill ok", title: hint }, el("span", { class: "dot", "aria-hidden": "true" }), "Online"),
    el("span", { class: "pill", title: hint }, `Default AI: ${b.ai_provider}`),
    el("span", { class: "pill", title: hint }, `Self-healing ${b.healing_enabled ? "on" : "off"}`));
}

function renderSources() {
  const groups = { runs: "Demo runs", recorded: "Recorded evidence (versioned)", workflows: "Backend workflows" };
  const children = [];
  for (const [collection, label] of Object.entries(groups)) {
    const items = state.sources.filter((s) => s.collection === collection);
    if (!items.length) continue;
    children.push(el("optgroup", { label }, items.map((s) => {
      const mode = s.presentation ? s.presentation.mode : "invalid";
      const prefix = mode === "live" ? "● LIVE · " : s.valid ? "" : "⚠ ";
      const when = (s.updated_at || "").replace("T", " ").slice(0, 16);
      return el("option", { value: keyOf(s), selected: keyOf(s) === state.current, disabled: !s.valid }, `${prefix}${s.title} · ${when}`);
    })));
  }
  fill($("source-select"), children);
}

function renderContext() {
  const d = state.detail;
  const sc = scenario();
  fill($("badges"), d ? [badge(d.presentation.mode, d.presentation.text, d.presentation.mode === "live"), badge(d.ai_badge.kind, d.ai_badge.text)] : []);
  $("replay").hidden = !(d && !isLive() && sc && sc.state === "finished");
  $("replay").textContent = state.replay ? "■ Stop replay" : "▶ Replay evidence";
  const raw = $("raw-link");
  raw.hidden = !d;
  if (d) raw.setAttribute("href", `${API}/sources/${encodeURIComponent(d.collection)}/${encodeURIComponent(d.name)}`);
  $("replay-banner").hidden = !state.replay;
  $("replay-banner").textContent = state.replay ? "REPLAY of saved evidence · not a live execution" : "";
}

function renderScenarios() {
  const b = bundle();
  const current = (scenario() || {}).key;
  fill($("scenarios"), b.scenarios.map((sc) => {
    const o = scenarioOutcome(sc);
    const selected = sc.key === current;
    const btn = el("button", { type: "button", class: `scenario ${o.cls}`, role: "tab", "aria-selected": selected ? "true" : "false", tabindex: selected ? "0" : "-1", "data-key": sc.key },
      el("span", { class: "avatar", "aria-hidden": "true" }, sc.key),
      el("span", { class: "key" }, `Scenario ${sc.key}`), el("span", { class: "title" }, cleanTitle(sc)), el("span", { class: "st" }, o.text));
    btn.addEventListener("click", () => chooseScenario(sc.key));
    return btn;
  }));
}

function chooseScenario(key) {
  stopReplay();
  state.scenarioKey = key;
  state.userPickedScenario = true;
  state.pinned = null;
  render();
}

// -- demo view ---------------------------------------------------------------------------

function renderHero() {
  const b = bundle();
  const w = b.workflow;
  $("wf-name").textContent = `Learned workflow · ${w.name}`;
  const recovered = b.scenarios.map((sc) => ({ sc, step: sc.steps.find((s) => s.recovery && s.recovery.outcome === "recovered") })).find((x) => x.step);
  const attempted = b.scenarios.some((sc) => sc.steps.some((s) => s.recovery));
  const ib = state.detail.interpretation_badge;
  const cap = (cls, ico, k, s) => el("li", { class: `cap ${cls}` }, el("span", { class: "ico", "aria-hidden": "true" }, ico), el("span", { class: "k" }, k), el("span", { class: "s" }, s));
  fill($("capabilities"),
    cap("learn", "◉", "Learns", `${plural(w.steps.length, "step")} · ${plural(w.inputs.length, "parameter")}`),
    cap("understand", "✦", "Understands", ib.kind === "real" ? "With Meta Muse Spark" : ib.kind === "simulated" ? "Simulated interpretation" : "No interpretation stored"),
    cap("recover", "↺", "Recovers", recovered ? `Recovered ${recovered.step.id} in scenario ${recovered.sc.key}` : attempted ? "Recovery attempted, not applied" : "No recovery in this evidence"));
}

function renderHow() {
  const sc = scenario();
  const ms = sc && sc.started_at && sc.finished_at ? Date.parse(sc.finished_at) - Date.parse(sc.started_at) : null;
  fill($("how-meta"),
    isLive() ? el("span", { class: "muted" }, "● A live run is in progress; this page follows the demo runner.")
      : ms !== null && sc.state === "finished" ? el("span", { class: "muted" }, `⏱ Scenario ${sc.key} took ${fmtMs(ms)} in this run.`)
        : el("span", { class: "muted" }, "Run timing is not stored in this evidence."));
}

function renderFlow(groups, focusGroup) {
  fill($("flow"), groups.map((g, gi) => {
    const mark = { completed: "✓", failed: "!", recovered: "★" }[g.state] || String(gi + 1);
    const node = el("button", {
      type: "button", class: `node ${g.state}${gi === focusGroup ? " focus" : ""}`,
      "aria-label": `Page ${gi + 1}: ${g.name} · ${STATE_TEXT[g.state]}`, "aria-current": gi === focusGroup ? "step" : null, title: g.path,
    },
    el("span", { class: "dotn", "aria-hidden": "true" }, mark),
    el("span", { class: "nl" }, el("strong", {}, g.name), el("small", {}, `${plural(g.steps.length, "step")}${g.ms !== null ? ` · ${fmtMs(g.ms)}` : ""}`)));
    node.addEventListener("click", () => pin(g.idx[0]));
    return el("li", { class: g.state === "completed" || g.state === "recovered" ? "done" : "" }, node);
  }));
}

function renderStepList(steps, groups, idx) {
  fill($("steps"), groups.map((g, gi) => el("li", { class: `pg ${g.state}` },
    el("div", { class: "pg-head" },
      el("span", { class: "dotn", "aria-hidden": "true" }, { completed: "✓", failed: "!", recovered: "★" }[g.state] || String(gi + 1)),
      el("div", {}, el("strong", {}, g.name), el("small", {}, g.path)),
      el("span", { class: "dur" }, g.ms !== null ? fmtMs(g.ms) : "")),
    el("ul", { class: "srows" }, g.idx.map((i) => {
      const s = steps[i];
      const row = el("button", { type: "button", class: `srow ${s.state}${i === idx ? " focus" : ""}`, "aria-current": i === idx ? "step" : null, title: `${s.id} · ${s.label || s.action}` },
        el("span", { class: "ic", "aria-hidden": "true" }, STEP_MARK[s.state] || "○"),
        el("span", { class: "tx" }, stepText(s), el("span", { class: "sr-only" }, ` · ${STATE_TEXT[s.state]}`)),
        el("span", { class: "ms" }, s.duration_ms !== null && s.duration_ms !== undefined && s.state !== "pending" ? fmtMs(s.duration_ms) : ""));
      row.addEventListener("click", () => pin(i));
      return el("li", {}, row);
    })))));
}

function pageElement(sc, s, focus) {
  const name = elementName(s.target || s.label);
  const cls = `pel${focus ? " focus" : ""}${s.state === "recovered" ? " recovered" : s.state === "failed" ? " failed" : ""}`;
  const done = s.state === "completed" || s.state === "recovered";
  if (s.action === "fill" || s.action === "select") {
    const value = done ? inputValue(sc, name) : null;
    return el("li", { class: cls }, el("div", { class: "lbl" }, name), el("div", { class: `field${value ? "" : " empty"}` }, value || (done ? "✓ filled" : "—")));
  }
  if (s.action === "check") return el("li", { class: cls }, el("span", { class: `tick${done ? "" : " pending"}` }, name));
  const kind = /^link\b/.test(s.target || "") ? " link" : "";
  const r = s.recovery;
  if (s.state === "recovered" && r && r.proposed) return el("li", { class: cls }, el("span", { class: `push${kind}` }, el("s", {}, name), "→", r.proposed.name || r.proposed.id));
  if (s.state === "failed") return el("li", { class: cls }, el("span", { class: "push gone" }, `${name} · not found`));
  return el("li", { class: cls }, el("span", { class: `push${kind}` }, name));
}

function renderPage(sc, steps, g, idx) {
  const items = g.idx.filter((i) => !PAGE_ACTIONS.has(steps[i].action)).map((i) => pageElement(sc, steps[i], i === idx));
  const arrival = g.steps.find((s) => PAGE_ACTIONS.has(s.action));
  const reached = arrival && arrival.state === "completed";
  fill($("page"),
    el("div", { class: "ptitle" }, g.name),
    el("div", { class: "psub" }, items.length ? `${g.path} · ${plural(items.length, "element")} GhostFlow used on this page` : g.path),
    items.length ? el("ul", { class: "els" }, items) : el("div", { class: `arrived${reached ? "" : " pending"}` }, reached ? "✓ Page reached" : "Waiting for this page"));
}

function renderAction(sc, steps, idx) {
  const step = steps[idx];
  if (!step) return fill($("action"));
  const [ico, verb] = ACTIONS[step.action] || ["•", step.action];
  const r = step.recovery;
  const name = step.target ? elementName(step.target) : whereOf(step) || (step.label || "");
  const recoveredSwap = step.state === "recovered" && r && r.proposed;
  const chip = step.phase === "recovering" ? ["running", "Recovering with AI…"] : [step.state, STATE_TEXT[step.state]];
  fill($("action"),
    el("div", { class: "verb" }, el("span", { class: "ico", "aria-hidden": "true" }, ico), verb),
    recoveredSwap
      ? el("div", { class: "swap" }, el("span", { class: "old" }, name), el("span", { class: "arrow", "aria-hidden": "true" }, "→"), el("span", { class: "new" }, r.proposed.name || r.proposed.id))
      : el("div", { class: "target" }, name),
    el("span", { class: `state-chip ${chip[0]}` }, chip[1]),
    el("div", { class: "meta" }, `Step ${idx + 1} of ${steps.length} · ${step.id}${step.duration_ms !== null && step.duration_ms !== undefined && step.state !== "pending" ? ` · ${fmtMs(step.duration_ms)}` : ""}`),
    step.state === "failed" ? el("p", { class: "note" }, r && r.outcome === "skipped" ? "Every recorded locator failed and self-healing is off: GhostFlow stopped instead of guessing." : (step.error || "The step failed.")) : null,
    sc.status === "error" && sc.error ? el("p", { class: "note err" }, sc.error) : null);
}

function renderEvents(sc, steps) {
  const target = healingStep(sc);
  const idx = target ? sc.steps.indexOf(target) : -1;
  const reached = target && (replayDone(sc) || state.replay.index > idx);
  const ev = (cls, icon, title, text, extra) => el("li", { class: `event ${cls}` },
    el("span", { class: "ei", "aria-hidden": "true" }, icon), el("div", {}, el("strong", {}, title), el("span", {}, text)), extra || el("span", {}));
  const items = [];
  if (sc.status === "error" && sc.error && replayDone(sc)) {
    items.push(ev("warn", "!", "Execution error", sc.error));
  } else if (target && (reached || steps[idx].phase === "recovering")) {
    const r = target.recovery;
    const n = (r ? r.exhausted_strategies : target.exhausted_strategies || []).length;
    items.push(ev("warn", "⚠", "Recorded element not found", `${elementName(target.target || target.label)} · ${plural(n, "recorded locator")} found nothing on the page.`));
    if (!r || !reached) {
      items.push(ev("ai", "✦", "Looking for an equivalent…", "Extracting the page's real candidates for the advisor."));
    } else if (r.outcome === "recovered" && r.proposed) {
      const passed = r.validation.filter((c) => c.passed).length;
      items.push(ev("ai", "✦", `${r.provider === "meta" ? "Muse Spark" : "Offline mock (simulated)"} proposed an alternative`,
        `Chose "${r.proposed.name || r.proposed.id}". GhostFlow verified ${passed}/${r.validation.length} checks and acted once.`,
        r.confidence ? el("span", { class: "status-chip ok" }, `Confidence ${r.confidence}`) : null));
    } else if (r.outcome === "skipped") {
      items.push(ev("", "■", "Stopped safely", `${r.reason || "Self-healing not attempted"}. GhostFlow did not guess.`));
    } else {
      items.push(ev("warn", "✕", `Recovery ${r.outcome}`, r.reason || "The proposal was not applied; the run stopped safely."));
    }
  } else if (sc.state === "finished" && replayDone(sc) && sc.status === "passed") {
    items.push(ev("ok", "✓", "All recorded elements found", "Every element was found with its recorded locators; the AI was not needed."));
  }
  fill($("events"), items);
}

function renderScenarioHead(sc) {
  const o = scenarioOutcome(sc);
  const done = replayDone(sc) && sc.state === "finished";
  const heal = healingStep(sc);
  const r = heal && heal.recovery;
  fill($("sc-title"), `Scenario ${sc.key} — ${cleanTitle(sc)}`);
  const failed = sc.steps.find((s) => s.state === "failed");
  $("sc-sub").textContent = !done ? (replaying(sc) ? "Replaying the saved steps one by one." : "The run is in progress.")
    : sc.status === "error" ? "The run stopped with an execution error before finishing."
      : o.cls === "recovered" ? `A recorded element was not found; GhostFlow recovered ${recoveredSteps(sc).map((s) => s.id).join(", ")} and completed the workflow.`
        : sc.status === "failed" && r && r.outcome === "skipped" ? `A recorded element was not found at ${failed ? failed.id : "a step"} and self-healing was off: GhostFlow stopped safely.`
          : sc.status === "failed" ? `The run stopped safely${failed ? ` at ${failed.id}` : ""}.`
            : "Every element was found with its recorded locators.";

  const cls = { passed: "ok", recovered: "heal", failed: "fail", error: "fail" }[o.cls] || "muted";
  const text = !done ? (replaying(sc) ? "Replaying…" : o.text) : sc.status === "failed" ? `Stopped safely · ${o.text.toLowerCase()}` : o.text;
  const ms = sc.started_at && sc.finished_at ? Date.parse(sc.finished_at) - Date.parse(sc.started_at) : null;
  const d = state.detail;
  const metric = (k, v) => el("div", { class: "metric" }, el("span", {}, k), el("strong", {}, v));
  fill($("result-summary"),
    el("span", { class: `status-chip ${done ? cls : "muted"}` }, text),
    el("div", { class: "metrics" },
      metric("Total time", done && ms !== null ? fmtMs(ms) : "—"),
      metric("AI advisor", r && r.consulted_model ? (r.provider === "meta" ? "Real Meta" : "Simulated") : "Not used"),
      metric("Workflow DSL", d.workflow_unchanged === true ? "Unchanged" : d.workflow_unchanged === false ? "Changed" : "Pending")));

  const verif = done ? sc.verifications : [];
  fill($("verifs"), verif.length ? [el("strong", {}, "Final checks"), ...verif.map((v) => el("span", {},
    el("span", { class: v.status === "passed" ? "ok-text" : v.status === "failed" ? "fail-text" : "muted" }, v.status === "passed" ? "✓ " : v.status === "failed" ? "✕ " : "– "),
    bundle().workflow.verify[v.index] || v.type, v.status === "skipped" ? " (not run)" : ""))] : []);
}

function renderStage() {
  const b = bundle();
  const sc = scenario();
  if (!sc) return;
  const steps = visibleSteps(sc);
  const idx = focusIndex(sc, steps);
  const groups = pageGroups(steps);
  const gi = Math.max(0, groups.findIndex((g) => g.idx.includes(idx)));

  renderFlow(groups, gi);
  renderScenarioHead(sc);
  renderStepList(steps, groups, idx);
  $("address").textContent = locationAt(steps, idx, b) || "—";
  renderPage(sc, steps, groups[gi], idx);
  renderAction(sc, steps, idx);
  renderEvents(sc, steps);
  $("stage-caption").textContent = isLive()
    ? "Live from the demo runner · the real browser is the Chromium window"
    : replaying(sc) ? "Replay of saved evidence · reconstructed, not a screenshot" : "Reconstructed from saved evidence · not a screenshot";

  const mood = moodFor(sc, steps[idx]);
  $("mascot").setAttribute("data-mood", mood);
  $("mascot-title").textContent = `GhostFlow mascot: ${MOOD_TEXT[mood]}`;
  $("mascot-say").textContent = MOOD_TEXT[mood].charAt(0).toUpperCase() + MOOD_TEXT[mood].slice(1);
}

function renderHealSummary() {
  const sc = scenario();
  const box = $("heal-summary");
  const title = el("h2", { id: "heal-summary-title" }, "Self-Healing with AI");
  if (!sc) return fill(box, el("div", { class: "summary-head" }, title));
  const target = healingStep(sc);
  const steps = visibleSteps(sc);
  const idx = target ? sc.steps.indexOf(target) : -1;
  const reached = target && (replayDone(sc) || state.replay.index > idx);
  const auditLink = el("button", { type: "button", class: "link-btn" }, "Open the full audit in Developer view");
  // What this card shows is the selected evidence, not the current backend settings in the header.
  const origin = el("p", { class: "eyebrow-s" }, isLive() ? `Live recovery · this run, scenario ${sc.key}` : `Recorded recovery · selected evidence, scenario ${sc.key}`);
  auditLink.addEventListener("click", () => setView("developer"));
  const original = (t, r) => [
    el("div", { class: "lbl-fail" }, "Original element (not found)"),
    el("div", { class: "codebox" }, t.target || t.label || t.id, r && r.exhausted_strategies.length ? el("small", {}, `${plural(r.exhausted_strategies.length, "recorded locator")} exhausted`) : null),
  ];

  if (!target) {
    const msg = sc.status === "error" ? "The run stopped before self-healing was relevant."
      : sc.state === "finished" ? "Not needed in this scenario: every element was found with its recorded locators, so the AI was not consulted." : "Waiting for the run…";
    return fill(box, el("div", { class: "summary-head" }, title, el("span", { class: "badge none" }, sc.healing ? "Enabled" : "Off")),
      el("p", { class: "eyebrow-s" }, isLive() ? `Live run · scenario ${sc.key}` : `Recorded result · selected evidence, scenario ${sc.key}`), el("p", { class: "explain" }, msg));
  }
  const r = target.recovery;
  if (!r || (steps[idx].phase === "recovering" && !reached)) {
    return fill(box, el("div", { class: "summary-head" }, title, el("span", { class: "chip rec" }, "In progress")), origin,
      el("p", { class: "explain" }, "Extracting the page's real candidates and asking the advisor to pick one."),
      el("div", { class: "col" }, original(target, r), el("div", { class: "down", "aria-hidden": "true" }, "↓"), el("div", { class: "lbl-blue" }, "Searching for an equivalent…")));
  }
  if (!reached) return fill(box, el("div", { class: "summary-head" }, title), origin, el("p", { class: "explain" }, `Replay in progress · step ${target.id} not reached yet.`));

  const recovered = r.outcome === "recovered";
  const real = r.provider === "meta";
  const passed = r.validation.filter((c) => c.passed).length;
  const levels = ["low", "medium", "high"];
  const advisor = real ? "Meta Muse Spark" : "The offline mock (simulated)";
  const explain = recovered ? `The recorded element was not found. ${advisor} chose an equivalent and GhostFlow verified it before acting once.`
    : r.outcome === "skipped" ? "The recorded element was not found and self-healing was off for this scenario, so GhostFlow stopped instead of guessing."
      : `The proposal was ${r.outcome}; GhostFlow stopped safely instead of acting.`;
  const alternatives = r.candidates && r.candidates.length
    ? [el("div", { class: "lbl-blue" }, `Alternatives on the page (${r.candidates.length})`),
      el("ul", { class: "cand-list" }, r.candidates.map((c) => {
        const chosen = recovered && r.proposed && r.proposed.id === c.id;
        return el("li", { class: chosen ? "chosen" : "" }, el("span", { class: "cid" }, c.id), el("span", { class: "cn" }, c.name || "(no name)"), chosen ? el("span", { class: "pick" }, "✓ Selected") : el("span", {}));
      }))]
    : r.consulted_model
      ? [el("div", { class: "lbl-blue" }, recovered && r.proposed ? `Chosen: ${r.proposed.name || r.proposed.id}` : "Alternatives"),
        el("p", { class: "note" }, `${plural(r.candidates_considered || 0, "candidate")} examined (this evidence did not store the list).`)]
      : [el("div", { class: "lbl-blue" }, "Alternatives"), el("p", { class: "note" }, "Not searched: the AI was not consulted.")];
  fill(box,
    el("div", { class: "summary-head" }, title, r.consulted_model ? badge(real ? "real" : "simulated", real ? "Real" : "Mock") : badge("none", "Off")),
    origin,
    el("p", { class: "explain" }, explain),
    el("div", { class: "col" }, original(target, r), el("div", { class: "down", "aria-hidden": "true" }, "↓"), alternatives),
    el("div", { class: "col" },
      el("dl", { class: "kv" },
        el("dt", {}, "Advisor"), el("dd", {}, r.consulted_model ? badge(real ? "real" : "simulated", real ? `Real · Meta ${r.model}` : `Simulated · ${r.model}`) : "Not consulted"),
        r.consulted_model ? [el("dt", {}, "Confidence"), el("dd", {}, el("span", { class: "meter", "aria-hidden": "true" }, levels.map((lvl, i) => el("i", { class: levels.indexOf(r.confidence) >= i ? "on" : "" }))), r.confidence || "none")] : null,
        r.validation.length ? [el("dt", {}, "Checks"), el("dd", { class: passed === r.validation.length ? "ok-text" : "fail-text" }, `${passed}/${r.validation.length} deterministic checks passed`)] : null,
        r.consulted_model && r.latency_ms !== null && r.latency_ms !== undefined ? [el("dt", {}, "Latency"), el("dd", {}, `${r.latency_ms} ms`)] : null,
        !r.consulted_model && r.reason ? [el("dt", {}, "Reason"), el("dd", {}, r.reason)] : null),
      el("div", { class: `info${r.consulted_model && real ? " real" : ""}` }, el("span", { class: "i", "aria-hidden": "true" }, "i"),
        el("span", {}, !r.consulted_model ? "The AI was not consulted in this scenario."
          : real ? "A real Meta Model API request, recorded in this evidence."
            : "Simulated recovery by the offline mock. No Meta request was made."))),
    auditLink);
}

// Interpretation (GF-002) is optional context for self-healing (GF-003), never a requirement.
function independenceNote(b) {
  const healed = b.scenarios.some((sc) => sc.steps.some((s) => s.recovery && s.recovery.consulted_model));
  return healed ? el("p", { class: "provenance" }, "The recorded recovery (GF-003) is independent of the interpretation (GF-002): it ran on this same workflow without one, using the recorded step, the page's real candidates and GhostFlow's deterministic checks.") : null;
}

function renderUnderstand() {
  const b = bundle();
  const i = b.workflow.interpretation;
  const ib = state.detail.interpretation_badge;
  const box = $("understand");
  const head = el("div", { class: "summary-head" }, el("h2", { id: "understand-title" }, "Understand · Meta Intelligence"), badge(ib.kind, ib.kind === "real" ? "Real" : ib.kind === "simulated" ? "Simulated" : "None"));
  if (!i) {
    box.className = "card understand compact";
    return fill(box, el("div", { class: "u-head" }, head, el("p", { class: "note strong" }, "No AI interpretation stored for this workflow."),
      el("p", { class: "note" }, `${b.workflow.source}. This panel only shows what a model actually returned.`), independenceNote(b)));
  }
  box.className = "card understand";
  const byId = Object.fromEntries(b.workflow.steps.map((s) => [s.id, s]));
  const critical = (i.steps || []).filter((s) => s.relevance === "critical");
  const healed = b.scenarios.some((sc) => sc.steps.some((s) => s.recovery && s.recovery.consulted_model));
  const fact = (k, ...v) => el("div", { class: "fact" }, el("div", { class: "k" }, k), ...v);
  fill(box,
    el("div", { class: "u-head" }, head, el("p", { class: "purpose" }, i.purpose)),
    el("div", { class: "facts" },
      fact("Expected outcome", i.expected_outcome || "—"),
      fact("Critical steps", critical.length ? critical.map((s) => (byId[s.id] && byId[s.id].target ? elementName(byId[s.id].target) : s.id)).join(" · ") : "—"),
      fact("Preconditions", `${(i.preconditions || []).length}`, (i.preconditions || [])[0] ? el("div", { class: "muted" }, i.preconditions[0]) : null),
      fact("Review notes", `${(i.review_notes || []).length} for a human reviewer`)),
    el("p", { class: "provenance" },
      `Interpretation: ${i.provider} · ${i.model}, generated ${utc(i.generated_at)} when the workflow was compiled (GF-002).`,
      healed ? " The recovery shown above is a separate execution with its own provenance." : ""));
}

// -- developer view ------------------------------------------------------------------------

function renderTimeline() {
  const sc = scenario();
  if (!sc) return fill($("timeline"));
  const steps = visibleSteps(sc);
  const runError = sc.status === "error" && sc.error ? el("li", { class: "step failed" }, el("span", { class: "mark", "aria-hidden": "true" }, "!"), el("div", { class: "body" }, el("div", { class: "label" }, "Execution error"), el("div", { class: "err" }, sc.error)), el("div", {})) : null;
  fill($("timeline"), runError, steps.map((s) => {
    const chips = [];
    if (s.phase === "recovering") chips.push(el("span", { class: "chip rec" }, "Asking AI…"));
    if (s.state === "recovered") chips.push(el("span", { class: "chip ai" }, "AI recovered"));
    if (s.resolution === "deterministic_fallback") chips.push(el("span", { class: "chip fb" }, "Fallback locator"));
    if (s.state === "failed") chips.push(el("span", { class: "chip fail" }, "Failed"));
    const mark = { completed: "✓", failed: "✕", recovered: "★" }[s.state] || "";
    return el("li", { class: `step ${s.state}`, "aria-label": `${s.id} ${STATE_TEXT[s.state]}` },
      el("span", { class: "mark", "aria-hidden": "true" }, mark),
      el("div", { class: "body" },
        el("div", { class: "label" }, s.label || s.action),
        el("div", { class: "meta" }, `${s.id} · ${s.action}${s.target ? ` · ${s.target}` : ""}${s.duration_ms != null && s.state !== "pending" ? ` · ${s.duration_ms} ms` : ""} · ${STATE_TEXT[s.state]}`),
        s.state === "failed" && s.error ? el("div", { class: "err" }, s.error) : null),
      el("div", {}, chips));
  }));
  $("timeline-title").textContent = `Execution · scenario ${sc.key}`;
}

function renderHealing() {
  const sc = scenario();
  const box = $("healing");
  const outcomeBox = $("healing-outcome");
  fill(outcomeBox);
  if (!sc) return fill(box);
  const steps = visibleSteps(sc);
  const target = healingStep(sc);
  const idx = target ? sc.steps.indexOf(target) : -1;
  const reached = target && (replayDone(sc) || state.replay.index > idx);
  const showing = target && steps[idx];

  if (!target && sc.status === "error") {
    return fill(box, el("p", { class: "note strong" }, "The run stopped before self-healing was relevant."), el("p", { class: "err" }, sc.error || "Execution error"));
  }
  if (!target) {
    const anyFallback = sc.steps.some((s) => s.resolution === "deterministic_fallback");
    return fill(box, el("p", { class: "note strong" }, sc.state === "finished" ? "No recovery needed." : "Waiting for the run…"),
      el("p", { class: "note" }, sc.state === "finished"
        ? (anyFallback ? "Some steps used a later recorded locator (deterministic fallback, no AI)." : "Every element was found with its recorded locator. The AI was not consulted.")
        : "Self-healing only starts when every recorded locator of a step finds nothing."));
  }
  const r = target.recovery;
  const inProgress = !r || (showing && showing.phase === "recovering" && !reached);
  if (inProgress) {
    const provider = r ? r.provider : (bundle().ai.provider === "meta" ? "meta" : "mock");
    fill(outcomeBox, el("span", { class: "chip rec" }, "In progress"));
    return fill(box, el("p", { class: "note strong" }, `Step ${target.id}: ${elementName(target.target || target.label)} is gone from the page.`),
      el("h3", {}, "Recorded locators that failed"), el("ul", { class: "strategies" }, (r ? r.exhausted_strategies : target.exhausted_strategies || []).map((x) => el("li", {}, x))),
      el("p", { class: "note" }, `Extracting the page's real candidates and asking ${provider === "meta" ? "Meta Muse Spark" : "the offline mock (simulated)"} to pick one…`));
  }
  if (!reached) return fill(box, el("p", { class: "note" }, `Replay in progress · step ${target.id} not reached yet.`));

  const recovered = r.outcome === "recovered";
  fill(outcomeBox, el("span", { class: `chip ${recovered ? "ai" : r.outcome === "skipped" ? "muted" : "fail"}` }, { recovered: "Recovered", rejected: "Rejected", skipped: "Not attempted", error: "Error" }[r.outcome] || r.outcome));
  const parts = [
    el("dl", { class: "kv" },
      el("dt", {}, "Recorded element"), el("dd", {}, target.target || target.label || "—"),
      el("dt", {}, "Chosen element"), el("dd", {}, recovered && r.proposed ? `${r.proposed.role || r.proposed.kind} "${r.proposed.name || r.proposed.id}"${r.proposed.href_path ? ` → ${r.proposed.href_path}` : ""}` : "—"),
      el("dt", {}, "Outcome"), el("dd", {}, `${r.outcome}${r.reason ? ` · ${r.reason}` : ""}`),
      el("dt", {}, "Attempt time"), el("dd", {}, `${r.duration_ms} ms`)),
    el("h3", {}, `Recorded locators exhausted (${r.exhausted_strategies.length})`), el("ul", { class: "strategies" }, r.exhausted_strategies.map((x) => el("li", {}, x))),
  ];
  if (r.candidates && r.candidates.length) {
    parts.push(el("h3", {}, `Candidates GhostFlow extracted (${r.candidates.length})`), el("ul", { class: "cands" }, r.candidates.map((c) =>
      el("li", { class: `cand ${r.proposed && r.proposed.id === c.id ? "chosen" : ""}` }, el("div", { class: "cid" }, `${c.id} · ${c.role || c.kind}`), el("div", { class: "cn" }, c.name || "(no name)"), c.href_path ? el("div", { class: "cp" }, c.href_path) : null))));
  } else if (r.candidates_considered) {
    parts.push(el("h3", {}, "Candidates GhostFlow extracted"), el("p", { class: "note" }, `${r.candidates_considered} eligible candidates examined (this evidence version did not store the list).`));
  }
  if (r.consulted_model) {
    const real = r.provider === "meta";
    parts.push(el("h3", {}, "Model proposal"), el("figure", { class: "model-says" },
      el("figcaption", { class: "who" }, badge(real ? "real" : "simulated", real ? `Meta ${r.model}` : `Simulated · ${r.model}`), r.latency_ms != null ? `${r.latency_ms} ms` : null, r.prompt_tokens != null ? `· ${r.prompt_tokens} in / ${r.completion_tokens} out tokens` : null),
      r.justification ? el("q", {}, r.justification) : el("span", { class: "muted" }, "No justification returned."),
      el("div", { class: "confidence" }, "Confidence", el("span", { class: "meter", "aria-label": `confidence ${r.confidence}` }, ["low", "medium", "high"].map((lvl, i) => el("i", { class: ["low", "medium", "high"].indexOf(r.confidence) >= i ? "on" : "" }))), el("strong", {}, r.confidence || "none"))));
  } else {
    parts.push(el("h3", {}, "Model"), el("p", { class: "note" }, `Not consulted. ${r.reason || ""}`));
  }
  if (r.validation && r.validation.length) {
    parts.push(el("h3", {}, "Deterministic validation by GhostFlow"), el("ul", { class: "checks" }, r.validation.map((c) =>
      el("li", { class: c.passed ? "pass" : "nopass" }, el("span", { class: "ic", "aria-hidden": "true" }, c.passed ? "✓" : "✕"), el("span", {}, CHECK_LABELS[c.check] || c.check, el("span", { class: "sr-only" }, c.passed ? " (passed)" : " (failed)")), el("span", { class: "d" }, c.detail || "")))));
  }
  parts.push(el("p", { class: "authority" }, recovered
    ? "The model proposed; GhostFlow verified every rule and acted once. The workflow definition was not modified."
    : `Recovery ${r.outcome}: the run stopped safely instead of guessing.`));
  fill(box, parts);
}

function renderIntelligence() {
  const b = bundle();
  const i = b.workflow.interpretation;
  const ib = state.detail.interpretation_badge;
  fill($("intel-badge"), badge(ib.kind, ib.kind === "real" ? "Real" : ib.kind === "simulated" ? "Simulated" : "None"));
  if (!i) {
    return fill($("intel"), el("p", { class: "note strong" }, "No AI interpretation for this workflow."),
      el("p", { class: "note" }, `${b.workflow.source}. GhostFlow shows only what a model actually returned; it never fills this panel with invented text.`), independenceNote(b));
  }
  const byId = Object.fromEntries(b.workflow.steps.map((s) => [s.id, s]));
  const critical = (i.steps || []).filter((s) => s.relevance === "critical");
  fill($("intel"),
    el("p", {}, i.purpose),
    el("dl", { class: "kv" },
      el("dt", {}, "Expected outcome"), el("dd", {}, i.expected_outcome || "—"),
      el("dt", {}, "Model"), el("dd", {}, `${i.provider} · ${i.model}`),
      el("dt", {}, "Generated"), el("dd", {}, utc(i.generated_at))),
    i.preconditions && i.preconditions.length ? [el("h3", {}, "Preconditions"), el("ul", { class: "plain" }, i.preconditions.map((p) => el("li", {}, p)))] : null,
    critical.length ? [el("h3", {}, "Critical steps"), el("ul", { class: "plain crit" }, critical.map((s) => el("li", {}, el("strong", {}, `${s.id} `), byId[s.id] && byId[s.id].label ? `${byId[s.id].label} — ` : "", s.explanation)))] : null,
    i.review_notes && i.review_notes.length ? [el("h3", {}, "Review notes"), el("ul", { class: "plain" }, i.review_notes.map((n) => el("li", {}, n)))] : null,
    el("p", { class: "authority" }, "Interpretation documents the workflow for people. The executor never reads it as instructions."));
}

function renderEvidence() {
  const b = bundle();
  const sc = scenario();
  const d = state.detail;
  if (!sc) return fill($("evidence"));
  const done = replayDone(sc) && sc.state === "finished";
  const healStep = healingStep(sc);
  const r = healStep ? healStep.recovery : null;
  const consultedMeta = !!(r && r.consulted_model && r.provider === "meta");
  const usage = b.ai.usage;
  const inputs = Object.entries(sc.inputs || {});
  fill($("evidence"),
    el("dl", { class: "kv" },
      el("dt", {}, "Workflow result"), el("dd", {}, done ? el("strong", { class: sc.status === "passed" ? "ok-text" : "fail-text" }, sc.status.toUpperCase()) : el("span", { class: "muted" }, "in progress")),
      el("dt", {}, "Meta consulted"), el("dd", {}, r && r.consulted_model ? (consultedMeta ? "Yes · real Meta Model API request" : `No · simulated by ${r.provider}`) : "No"),
      el("dt", {}, "Provider · model"), el("dd", {}, r && r.provider ? `${r.provider} · ${r.model}` : sc.healing ? "—" : "Not used: self-healing off in this scenario"),
      el("dt", {}, "Latency · tokens"), el("dd", {}, r && r.latency_ms != null ? `${r.latency_ms} ms · ${r.prompt_tokens ?? "?"} in / ${r.completion_tokens ?? "?"} out (${r.reasoning_tokens ?? 0} reasoning)` : "—"),
      el("dt", {}, "Meta requests (demo)"), el("dd", {}, `${b.ai.requests_sent}${b.ai.request_cap ? ` of max ${b.ai.request_cap}` : ""}${usage ? ` · ${usage.prompt_tokens + usage.completion_tokens} tokens` : ""}`),
      el("dt", {}, "DSL unchanged"), el("dd", {}, d.workflow_unchanged === true ? el("span", { class: "ok-text" }, "✓ same SHA-256 before and after") : d.workflow_unchanged === false ? el("span", { class: "fail-text" }, "✕ changed") : el("span", { class: "muted" }, "pending")),
      el("dt", {}, "SHA-256"), el("dd", { class: "sha" }, b.workflow.sha256_before || "—"),
      el("dt", {}, "Started · finished"), el("dd", {}, sc.started_at ? `${utc(sc.started_at)} · ${utc(sc.finished_at)}` : "not stored"),
      el("dt", {}, "Blocked requests"), el("dd", {}, String(sc.blocked_requests.length))),
    el("h3", {}, "Final verifications"),
    sc.verifications.length && done
      ? el("ul", { class: "plain verif" }, sc.verifications.map((v) => el("li", {}, el("span", { class: v.status === "passed" ? "ok-text" : v.status === "failed" ? "fail-text" : "muted" }, v.status === "passed" ? "✓ " : v.status === "failed" ? "✕ " : "– "), `${b.workflow.verify[v.index] || v.type}`, v.status === "skipped" ? " (not run: an earlier step failed)" : "")))
      : el("p", { class: "note" }, "Not run yet."),
    inputs.length ? [el("h3", {}, "Input data for this run"), el("dl", { class: "kv" }, inputs.flatMap(([k, v]) => [el("dt", {}, k), el("dd", {}, v)]))] : null,
    el("h3", {}, "Provenance"), el("p", { class: "note" }, `${b.workflow.source}. ${b.provenance || ""}`),
    sc.derived_steps ? el("p", { class: "authority" }, "Step states before the failed or recovered step are derived from the stored summary (the executor stops at the first failure).") : null);
}

// -- views ---------------------------------------------------------------------------------

function setView(view) {
  state.view = view;
  const url = new URL(location.href);
  if (view === "developer") url.searchParams.set("view", "developer"); else url.searchParams.delete("view");
  history.replaceState(null, "", url);
  render();
  $(view === "developer" ? "tab-dev" : "tab-demo").focus();
}

function renderViews() {
  const dev = state.view === "developer";
  $("demo-view").hidden = dev;
  $("dev-view").hidden = !dev;
  for (const [id, on] of [["tab-demo", !dev], ["tab-dev", dev]]) {
    $(id).setAttribute("aria-selected", on ? "true" : "false");
    $(id).setAttribute("tabindex", on ? "0" : "-1");
  }
}

function render() {
  const has = !!state.detail;
  $("app").hidden = !has;
  $("empty").hidden = has;
  renderSystem();
  renderSources();
  renderContext();
  renderViews();
  if (!has) {
    fill($("scenarios"));
    $("empty").textContent = state.error || (state.sources.length
      ? "Choose an evidence source."
      : "No demo evidence yet. Run `make demo-rehearsal` (offline mock) or see docs/DEMO_RUNBOOK.md. Recorded evidence lives in docs/evidence.");
    return;
  }
  renderScenarios();
  renderHero();
  renderHow();
  renderStage();
  renderHealSummary();
  renderUnderstand();
  renderTimeline();
  renderHealing();
  renderIntelligence();
  renderEvidence();
  const b = bundle();
  $("source-hint").textContent = `Source: ${b.workflow.source}. ${b.provenance || ""}`;
}

// -- replay ------------------------------------------------------------------------------

function stopReplay() {
  clearTimeout(state.timers.replay);
  state.replay = null;
}

function startReplay() {
  const sc = scenario();
  if (!sc) return;
  stopReplay();
  state.pinned = null;
  state.replay = { key: sc.key, index: 0 };
  const tick = () => {
    const current = scenario();
    if (!state.replay || !current) return;
    state.replay.index += 1;
    if (state.replay.index > current.steps.length) { stopReplay(); render(); return; }
    render();
    const running = current.steps[state.replay.index];
    const slow = running && running.recovery && running.recovery.consulted_model ? 2400 : REPLAY_STEP_MS;
    state.timers.replay = setTimeout(tick, slow);
  };
  render();
  state.timers.replay = setTimeout(tick, REPLAY_STEP_MS);
}

// -- loading and polling -----------------------------------------------------------------

async function loadSources() {
  try {
    const data = await getJSON(`${API}/sources`);
    state.sources = data.sources;
    state.backend = data.backend;
    state.backendOnline = true;
  } catch (err) {
    state.backendOnline = false;
    renderSystem();
    $("source-hint").textContent = `Cannot reach the GhostFlow backend (${err.message}).`;
    return;
  }
  const live = state.sources.find((s) => s.presentation && s.presentation.mode === "live");
  if (live && $("follow").checked && keyOf(live) !== state.current) return select(keyOf(live));
  if (!state.current) {
    const first = state.sources.find((s) => s.valid && s.collection === "runs")
      || state.sources.find((s) => s.valid && s.collection === "recorded")
      || state.sources.find((s) => s.valid);
    const fromHash = decodeURIComponent(location.hash.slice(1));
    const wanted = state.sources.find((s) => keyOf(s) === fromHash && s.valid) || first;
    if (wanted) return select(keyOf(wanted));
  }
  if (!state.replay) render(); else { renderSystem(); renderSources(); }
}

async function loadDetail() {
  if (!state.current) return;
  const [collection, ...rest] = state.current.split("/");
  try {
    state.detail = await getJSON(`${API}/sources/${encodeURIComponent(collection)}/${encodeURIComponent(rest.join("/"))}`);
    state.error = null;
  } catch (err) {
    state.detail = null;
    state.error = `This evidence cannot be shown (${err.message}).`;
  }
  const b = bundle();
  if (b && !state.userPickedScenario) {
    const running = b.scenarios.find((s) => s.state === "running");
    const healed = b.scenarios.find((s) => s.steps.some((st) => st.recovery && st.recovery.outcome === "recovered"));
    state.scenarioKey = (running || (isLive() ? b.scenarios.filter((s) => s.state === "finished").pop() : null) || healed || b.scenarios[0] || {}).key || null;
  }
  render();
  clearTimeout(state.timers.live);
  if (isLive()) state.timers.live = setTimeout(loadDetail, LIVE_POLL_MS);
}

async function select(key) {
  stopReplay();
  state.current = key;
  state.userPickedScenario = false;
  state.scenarioKey = null;
  state.pinned = null;
  const url = new URL(location.href);
  url.hash = encodeURIComponent(key);
  history.replaceState(null, "", url);
  await loadDetail();
}

// Arrow keys move between tabs (WAI-ARIA tabs pattern; vertical lists also take Up/Down).
function arrowTabs(container, onPick) {
  container.addEventListener("keydown", (e) => {
    const forward = ["ArrowRight", "ArrowDown"], back = ["ArrowLeft", "ArrowUp"];
    if (![...forward, ...back, "Home", "End"].includes(e.key)) return;
    const tabs = [...container.querySelectorAll('[role="tab"]')];
    const i = tabs.indexOf(document.activeElement);
    if (i < 0) return;
    e.preventDefault();
    const next = e.key === "Home" ? 0 : e.key === "End" ? tabs.length - 1 : (i + (forward.includes(e.key) ? 1 : -1) + tabs.length) % tabs.length;
    onPick(tabs[next]);
  });
}

document.addEventListener("DOMContentLoaded", () => {
  $("source-select").addEventListener("change", (e) => select(e.target.value));
  $("replay").addEventListener("click", () => { if (state.replay) { stopReplay(); render(); } else startReplay(); });
  $("tab-demo").addEventListener("click", () => setView("demo"));
  $("tab-dev").addEventListener("click", () => setView("developer"));
  arrowTabs(document.querySelector(".segmented"), (tab) => setView(tab.id === "tab-dev" ? "developer" : "demo"));
  arrowTabs($("scenarios"), (tab) => { chooseScenario(tab.getAttribute("data-key")); const again = $("scenarios").querySelector('[aria-selected="true"]'); if (again) again.focus(); });
  loadSources();
  setInterval(loadSources, SOURCES_POLL_MS);
});
