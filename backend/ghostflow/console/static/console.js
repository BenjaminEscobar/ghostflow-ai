// GhostFlow Demo Console. Read-only: it only GETs /api/demo/* and renders with textContent
// (never HTML parsing), so nothing in an evidence file can become markup or code.
// Everything shown is derived from the evidence or the backend; nothing is invented.
"use strict";

const API = "/api/demo";
const LIVE_POLL_MS = 500; // only while a run is live (local loopback, small JSON)
// A recovery or a safe stop can happen between two polls (milliseconds with the mock). When a
// poll reveals one, the console dwells on that real event, in order, for this long; the run itself
// is never slowed down and nothing is invented: the image is the capture taken at that step.
const MOMENT_MS = 1600;
const MAX_MOMENTS = 4;
const SOURCES_POLL_MS = 3000;
const REPLAY_STEP_MS = 650;
const REPLAY_STAGE_MS = 1500; // playback pace of a recorded recovery; not the real duration

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
  idle: "waiting", working: "working on a step", investigating: "investigating, recovery in progress",
  evaluating: "reviewing the recorded checks (playback)", alert: "stopped safely",
  cautious: "proposal rejected, stopped safely", happy: "workflow completed", proud: "recovered safely",
};
const MOOD_LABEL = {
  idle: "Ready", working: "Working…", investigating: "Investigating… recovery in progress",
  evaluating: "Playback · reviewing recorded checks", alert: "Stopped safely", cautious: "Proposal rejected · stopped safely",
  happy: "Completed", proud: "Recovered safely",
};
const SKIP_TEXT = {
  sensitive_step: "a sensitive input is typed here, so it is never captured",
  sensitive_page: "a sensitive value was typed on this page, so nothing here is captured",
  off_origin: "the page is outside the local demo portal, so it is not captured",
  limit: "the capture limit for this run was reached",
  rate: "skipped by the capture rate limit",
  too_large: "the image exceeded the size limit",
  error: "the capture failed (the run was not affected)",
};
const END_OF_RUN = 1e9; // position of the final-page capture, after every step
const imgCache = new Map(); // capture id -> <img>, so polling never reloads an image
const failedShots = new Set(); // bounded by the selected bundle; do not retry a pruned image forever
const IMAGE_LIMITS = { full: 3, thumb: 80 };
let detailRequest = null;
let detailVersion = 0;
// Words for an event the console observed after it happened (past tense: it already finished).
const MOMENT_LABEL = {
  investigating: "Recovery started · recorded element missing", proud: "Recovered safely",
  alert: "Stopped safely", cautious: "Proposal rejected · stopped safely", happy: "Completed",
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
  replay: null, // {key, index, stage}
  stageView: "capture", // "capture" or "reconstructed", when captures exist
  shot: null, // id of a capture chosen in the filmstrip
  seen: {}, // live runs: last observed state of each scenario and step
  moments: [], // live runs: observed events still to show, in order
  moment: null, // {sc, idx, mood} being shown
  lastMood: null,
  expanded: false, // the browser is shown in the expanded dialog
  at: null, // {idx, count}: the step in focus, for the expanded view's progress and arrows
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

async function getJSON(path, signal) {
  const res = await fetch(path, { signal, cache: "no-store", headers: { accept: "application/json" } });
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
const momentOf = (sc) => (state.moment && state.moment.sc === sc.key && state.pinned === null && !replaying(sc) ? state.moment : null);
// A step's outcome is visible once the replay cursor passed it (or showed its result stage).
const reachedStep = (sc, idx) => replayDone(sc) || state.replay.index > idx || (state.replay.index === idx && state.replay.stage >= 2);
const isLive = () => !!(state.detail && state.detail.presentation.mode === "live");
const utc = (iso) => (iso ? `${iso.replace("T", " ").slice(0, 16)} UTC` : "—");
const fmtMs = (ms) => (ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`);
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
const cleanTitle = (sc) => sc.title.replace(/^[A-Z]\d* · /, "");
// Brief scenario titles from the scenario's own facts; the full stored title stays as a tooltip.
const shortTitle = (sc) => (sc.portal === "original" ? "Original"
  : sc.portal && sc.portal !== "configured origin" ? "Changed UI" : cleanTitle(sc));

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
  if (replaying(sc)) return { cls: "running", text: "Replaying saved evidence…", brief: "Replaying…" };
  if (sc.state === "pending") return { cls: "pending", text: "Pending", brief: "Pending" };
  if (sc.state === "running") return { cls: "running", text: "Running…", brief: "Running…" };
  if (sc.state === "interrupted") return { cls: "error", text: "Interrupted", brief: "Interrupted" };
  if (sc.status === "passed" && recoveredSteps(sc).length) return { cls: "recovered", text: `Recovered ${recoveredSteps(sc).map((s) => s.id).join(", ")} · completed`, brief: "Recovered" };
  if (sc.status === "passed") return { cls: "passed", text: "Completed", brief: "Completed" };
  const failed = sc.steps.find((s) => s.state === "failed");
  if (sc.status === "failed") return { cls: "failed", text: failed ? `Failed at ${failed.id}` : "Failed", brief: failed ? "Stopped safely" : "Failed" };
  return { cls: "error", text: "Error", brief: "Error" };
}

// Replay rewrites step states up to a cursor; live and saved views use the stored ones.
function visibleSteps(sc) {
  if (!replaying(sc)) return sc.steps;
  const cursor = state.replay.index;
  return sc.steps.map((s, i) => {
    if (i < cursor) return s;
    if (i === cursor) {
      const consulted = s.recovery && s.recovery.consulted_model;
      if (consulted && state.replay.stage >= 2) return s; // the recorded result
      if (consulted) return { ...s, state: "running", phase: state.replay.stage === 1 ? "evaluating" : "recovering", error: null };
      return { ...s, state: s.state === "skipped" ? "skipped" : "running", phase: null, error: null };
    }
    return { ...s, state: "pending", phase: null, error: null };
  });
}

function focusIndex(sc, steps) {
  if (replaying(sc)) return Math.min(state.replay.index, steps.length - 1);
  if (state.pinned !== null && state.pinned < steps.length) return state.pinned;
  if (momentOf(sc)) return Math.min(state.moment.idx, steps.length - 1);
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
    return { ...g, steps: ss, state: st, name: pageName(g.where), path: pathOf(g.where) };
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
  if (replayDone(sc) && (state.detail?.presentation.mode === "stale" || sc.state === "interrupted" || sc.status === "error" ||
      (sc.verifications || []).some((v) => v.status === "failed"))) return "alert";
  if (!step) return "idle";
  if (step.phase === "recovering") return "investigating"; // recovery_started: every locator found nothing
  if (step.phase === "evaluating") return "evaluating"; // playback of the recorded validation only
  if (step.state === "running") return "working";
  if (step.state === "failed" && step.recovery && ["rejected", "error"].includes(step.recovery.outcome)) return "cautious";
  if (step.state === "failed" || sc.status === "error") return "alert";
  if (sc.state === "finished" && replayDone(sc) && sc.status === "passed" && state.pinned === null) return recoveredSteps(sc).length ? "proud" : "happy";
  if (step.state === "recovered") return "proud";
  return "idle";
}

function reviewIssue(sc) {
  if (!replayDone(sc)) return null;
  if (state.detail?.presentation.mode === "stale") return "Updates interrupted · review required";
  if (sc.state === "interrupted") return "Run interrupted · review required";
  if (sc.status === "error") return "Execution error · review required";
  if ((sc.verifications || []).some((v) => v.status === "failed")) return "Verification failed · review required";
  const failed = sc.steps.find((s) => s.state === "failed");
  if (failed?.recovery?.outcome === "error") return "Recovery error · review required";
  if (failed && !failed.recovery) return "Step failed · review required";
  return null;
}

function stepText(s) {
  const verb = (ACTIONS[s.action] || ["", s.action])[1];
  if (s.action === "navigate") return `Open ${pathOf(whereOf(s)) || "page"}`;
  if (s.action === "wait_for_url") return `Reach ${whereOf(s) || "page"}`;
  if (s.state === "recovered" && s.recovery && s.recovery.proposed) return `${verb} ${s.recovery.proposed.name || s.recovery.proposed.id}`;
  return `${verb} ${elementName(s.target || s.label)}`;
}

function pin(i) {
  stopReplay();
  clearMoments();
  state.pinned = state.pinned === i ? null : i;
  state.shot = null;
  render();
}

function stepBy(delta) {
  if (!state.at || !scenario()) return;
  const i = state.at.idx + delta;
  if (i < 0 || i >= state.at.count) return;
  stopReplay();
  clearMoments();
  state.pinned = i;
  state.shot = null;
  render();
}

// -- header, sidebar, badges ------------------------------------------------------------------

function renderSystem() {
  const b = state.backend;
  const signature = JSON.stringify([state.backendOnline, b]);
  if ($("system-status").dataset.signature === signature) return;
  $("system-status").dataset.signature = signature; // announce actual settings changes only
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
  if (document.activeElement === $("source-select")) return; // keep an open keyboard menu stable
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
  const signature = JSON.stringify(d ? [d.presentation, d.ai_badge] : null);
  if ($("badges").dataset.signature !== signature) {
    $("badges").dataset.signature = signature;
    // The recovery badge is scoped to its stage ("Recovery · …"); the interpretation has its own
    // chip in the Demo view, so no single label speaks for all of the AI.
    fill($("badges"), d ? [badge(d.presentation.mode, d.presentation.text, d.presentation.mode === "live"), badge(d.ai_badge.kind, d.ai_badge.text)] : []);
  }
  $("replay").hidden = !(d && !isLive() && sc && sc.state === "finished");
  $("replay").textContent = state.replay ? "■ Stop replay" : "▶ Replay evidence";
  const raw = $("raw-link");
  raw.hidden = !d;
  if (d) raw.setAttribute("href", `${API}/sources/${encodeURIComponent(d.collection)}/${encodeURIComponent(d.name)}`);
  $("replay-banner").hidden = !state.replay;
  const banner = state.replay ? "REPLAY of saved evidence · not a live execution · playback pace, not the real duration" : "";
  if ($("replay-banner").textContent !== banner) $("replay-banner").textContent = banner;
}

function renderScenarios() {
  const b = bundle();
  const current = (scenario() || {}).key;
  fill($("scenarios"), b.scenarios.map((sc) => {
    const o = scenarioOutcome(sc);
    const selected = sc.key === current;
    const btn = el("button", { type: "button", class: `scenario ${o.cls}`, role: "tab", "aria-selected": selected ? "true" : "false", tabindex: selected ? "0" : "-1", "data-key": sc.key, "data-focus-key": `scenario-${sc.key}` },
      el("span", { class: "avatar", "aria-hidden": "true" }, sc.key),
      el("span", { class: "key sr-only" }, `Scenario ${sc.key} · `), el("span", { class: "title", title: cleanTitle(sc) }, shortTitle(sc)),
      el("span", { class: "st" }, o.brief, o.text !== o.brief ? el("span", { class: "sr-only" }, ` (${o.text})`) : null));
    btn.addEventListener("click", () => chooseScenario(sc.key));
    return btn;
  }));
}

function chooseScenario(key) {
  stopReplay();
  clearMoments(); // the user's choice wins over live moments
  state.scenarioKey = key;
  state.userPickedScenario = true;
  state.pinned = null;
  state.shot = null;
  render();
}

// -- demo view ---------------------------------------------------------------------------

// Capabilities and "How it works" left the Demo view: the browser comes first.
function renderHero() {
  const w = bundle().workflow;
  $("wf-name").textContent = `Learned workflow · ${w.name} · ${plural(w.steps.length, "step")}`;
  renderProvRow();
}

function renderFlow(groups, focusGroup) {
  fill($("flow"), groups.map((g, gi) => {
    const mark = { completed: "✓", failed: "!", recovered: "★" }[g.state] || String(gi + 1);
    const node = el("button", {
      type: "button", class: `node ${g.state}${gi === focusGroup ? " focus" : ""}`,
      "aria-label": `Page ${gi + 1}: ${g.name} · ${STATE_TEXT[g.state]}`, "aria-current": gi === focusGroup ? "step" : null, title: g.path, "data-focus-key": `page-${gi}`,
    },
    el("span", { class: "dotn", "aria-hidden": "true" }, mark),
    el("span", { class: "nl" }, el("strong", {}, g.name)));
    node.addEventListener("click", () => pin(g.idx[0]));
    return el("li", { class: g.state === "completed" || g.state === "recovered" ? "done" : "" }, node);
  }));
}

function renderStepList(steps, groups, idx) {
  fill($("steps"), groups.map((g, gi) => el("li", { class: `pg ${g.state}` },
    el("div", { class: "pg-head" },
      el("span", { class: "dotn", "aria-hidden": "true" }, { completed: "✓", failed: "!", recovered: "★" }[g.state] || String(gi + 1)),
      el("div", {}, el("strong", {}, g.name), el("small", {}, g.path))),
    el("ul", { class: "srows" }, g.idx.map((i) => {
      const s = steps[i];
      const row = el("button", { type: "button", class: `srow ${s.state}${i === idx ? " focus" : ""}`, "aria-current": i === idx ? "step" : null, title: `${s.id} · ${s.label || s.action}`, "data-focus-key": `step-${s.id}` },
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
  const signature = JSON.stringify([state.current, sc.key, idx, step, sc.status, sc.error]);
  if ($("action").dataset.signature === signature) return;
  $("action").dataset.signature = signature;
  const [ico, verb] = ACTIONS[step.action] || ["•", step.action];
  const r = step.recovery;
  const name = step.target ? elementName(step.target) : whereOf(step) || (step.label || "");
  const recoveredSwap = step.state === "recovered" && r && r.proposed;
  const chip = step.phase === "recovering" ? ["running", "Recovery in progress"] : step.phase === "evaluating" ? ["running", "Playback · checks"] : [step.state, STATE_TEXT[step.state]];
  fill($("action"),
    el("div", { class: "verb" }, el("span", { class: "ico", "aria-hidden": "true" }, ico), verb),
    recoveredSwap
      ? el("div", { class: "swap" }, el("span", { class: "old" }, name), el("span", { class: "arrow", "aria-hidden": "true" }, "→"), el("span", { class: "new" }, r.proposed.name || r.proposed.id))
      : el("div", { class: "target" }, name),
    el("span", { class: `state-chip ${chip[0]}` }, chip[1]),
    el("div", { class: "meta" }, `Step ${idx + 1} of ${steps.length} · ${step.id}`),
    step.state === "failed" ? el("p", { class: "note" }, r && r.outcome === "skipped" ? "Every recorded locator failed and self-healing is off: GhostFlow stopped instead of guessing." : (step.error || "The step failed.")) : null,
    sc.status === "error" && sc.error ? el("p", { class: "note err" }, sc.error) : null);
}

function renderEvents(sc, steps) {
  const target = healingStep(sc);
  const idx = target ? sc.steps.indexOf(target) : -1;
  const reached = target && reachedStep(sc, idx);
  const ev = (cls, icon, title, text, extra) => el("li", { class: `event ${cls}` },
    el("span", { class: "ei", "aria-hidden": "true" }, icon), el("div", {}, el("strong", {}, title), el("span", {}, text)), extra || el("span", {}));
  const items = [];
  if (sc.status === "error" && sc.error && replayDone(sc)) {
    items.push(ev("warn", "!", "Execution error", sc.error));
  } else if (target && (reached || steps[idx].phase)) {
    const r = target.recovery;
    const n = (r ? r.exhausted_strategies : target.exhausted_strategies || []).length;
    items.push(ev("warn", "⚠", "Recorded element not found", `${elementName(target.target || target.label)} · ${plural(n, "recorded locator")} found nothing on the page.`));
    if (!r || !reached) {
      items.push(steps[idx].phase === "evaluating"
        ? ev("ai", "✓", "Playback · GhostFlow's deterministic checks", `${plural(r.validation.length, "recorded check")}, replayed at presentation pace (not the real duration).`)
        : ev("ai", "✦", "Recovery in progress", "The outcome appears when GhostFlow's checks finish."));
    } else if (r.outcome === "recovered" && r.proposed) {
      const passed = r.validation.filter((c) => c.passed).length;
      items.push(ev("ai", "✦", `${r.provider === "meta" ? "Muse Spark" : "Offline mock (simulated)"} proposed an alternative`,
        `Chose "${r.proposed.name || r.proposed.id}". GhostFlow verified ${passed}/${r.validation.length} checks and acted once.`,
        r.confidence ? el("span", { class: "status-chip ok" }, `Confidence ${r.confidence}`) : null));
    } else if (r.outcome === "skipped") {
      items.push(ev("", "■", "Stopped safely", `${r.reason || "Self-healing not attempted"}. GhostFlow did not guess.`));
    } else {
      items.push(ev("warn", "✕", `Recovery ${r.outcome}`, r.reason || (r.outcome === "error"
        ? "Recovery error. Review the outcome before retrying." : "The proposal was not applied; the run stopped safely.")));
    }
  } else if (sc.state === "finished" && replayDone(sc) && sc.status === "passed") {
    items.push(ev("ok", "✓", "All recorded elements found", "Every element was found with its recorded locators; the AI was not needed."));
  }
  fill($("events"), items);
}

function renderScenarioHead(sc) {
  const o = scenarioOutcome(sc);
  const done = replayDone(sc) && sc.state === "finished";
  const issue = reviewIssue(sc);
  fill($("sc-title"), `Scenario ${sc.key} — ${shortTitle(sc)}`,
    typeof sc.healing === "boolean" ? el("span", { class: "sc-mode" }, ` · self-healing ${sc.healing ? "on" : "off"}`) : null);
  const cls = { passed: "ok", recovered: "ok", failed: "fail", error: "fail" }[o.cls] || "muted";
  const text = !done ? (replaying(sc) ? "Replaying…" : o.text) : issue || (sc.status === "failed" ? `Stopped safely · ${o.text.toLowerCase()}` : o.text);
  const ms = sc.started_at && sc.finished_at ? Date.parse(sc.finished_at) - Date.parse(sc.started_at) : null;
  const d = state.detail;
  const metric = (k, v) => el("span", { class: "metric" }, `${k} `, el("strong", {}, v));
  fill($("result-summary"),
    el("span", { class: `status-chip ${done ? cls : "muted"}` }, text),
    metric("Total time", done && ms !== null ? fmtMs(ms) : "—"),
    metric("Workflow", d.workflow_unchanged === true ? "Unchanged" : d.workflow_unchanged === false ? "Changed" : "Pending"));
  renderVerification(sc, done);
}

// The workflow's own final checks, as the evidence recorded them (every detail is in Developer).
const verifyText = (t) => (/^(?:text_visible|url_path): \w+=(.+)$/.exec(t) || [])[1] || t;
function renderVerification(sc, done) {
  const verif = done ? sc.verifications : [];
  if (!verif.length) return fill($("verifs"));
  const passed = verif.filter((v) => v.status === "passed").length;
  const ran = verif.some((v) => v.status !== "skipped");
  const [cls, text] = !ran ? ["muted", "Not run"] : [passed === verif.length ? "ok" : "fail", `${passed}/${verif.length} passed`];
  const ICON = { passed: "✓", failed: "✕" };
  fill($("verifs"),
    el("div", { class: "summary-head" }, el("h3", { id: "verifs-title" }, "Verification"), el("span", { class: `status-chip ${cls}` }, text)),
    el("ul", { class: "verif-list" }, verif.map((v) => el("li", { class: v.status },
      el("span", { class: "ic", "aria-hidden": "true" }, ICON[v.status] || "–"),
      el("span", { class: "vt" }, verifyText(bundle().workflow.verify[v.index] || v.type), v.status === "skipped" ? " (not run)" : "",
        el("span", { class: "sr-only" }, ` · ${v.status}`))))));
}

function renderStage() {
  const b = bundle();
  const sc = scenario();
  if (!sc) return;
  const steps = visibleSteps(sc);
  const idx = focusIndex(sc, steps);
  const groups = pageGroups(steps);
  const gi = Math.max(0, groups.findIndex((g) => g.idx.includes(idx)));
  state.at = { idx, count: steps.length };

  renderFlow(groups, gi);
  renderScenarioHead(sc);
  renderStepList(steps, groups, idx);
  renderPage(sc, steps, groups[gi], idx);
  renderAction(sc, steps, idx);
  renderEvents(sc, steps);
  const shown = renderCapture(sc, steps, idx);
  if (!shown) $("address").textContent = locationAt(steps, idx, b) || "—";
  $("address").title = $("address").textContent; // the full address when the bar truncates it
  $("stage-caption").textContent = shown
    ? (isLive() ? (momentOf(sc) ? `Live capture · just observed at step ${steps[idx].id}, the run continues`
      : "Live capture · real screenshot of the Chromium page GhostFlow controls, updated as the run progresses")
      : replaying(sc) ? "Replay of recorded captures · not a live execution"
        : "Recorded capture · real screenshot saved during this run, not a live view")
    : shotsOf(sc).some((c) => failedShots.has(c.id))
      ? "Capture unavailable · showing the reconstructed evidence; the execution is unaffected"
    : isLive() ? "Reconstructed view · Live from the demo runner · Chromium runs headless by default"
      : replaying(sc) ? "Replay of saved evidence · reconstructed, not a screenshot" : "Reconstructed from saved evidence · not a screenshot";

  const moment = momentOf(sc);
  const issue = reviewIssue(sc);
  const mood = issue ? moodFor(sc, steps[idx]) : moment ? moment.mood : moodFor(sc, steps[idx]);
  const label = issue || (moment ? MOMENT_LABEL[mood] : MOOD_LABEL[mood]);
  $("mascot").setAttribute("data-mood", mood);
  $("agent").setAttribute("data-mood", mood);
  $("mascot-title").textContent = `GhostFlow mascot: ${issue || moment ? label.toLowerCase() : MOOD_TEXT[mood]}`;
  $("mascot-say").textContent = label;
  if (state.lastMood !== mood) {
    state.lastMood = mood;
  }
  if (state.lastAgentLabel !== label) { // announce actual status changes once, not every poll
    state.lastAgentLabel = label;
    // While the dialog is open the page behind it is inert: announce from inside the dialog.
    $(state.expanded ? "xb-status" : "agent-status").textContent = `GhostFlow: ${label}`;
  }
}

// -- real browser captures (GF-006) ----------------------------------------------------------

const shotsOf = (sc) => (sc.captures || []).filter((c) => !c.skipped);

function captureImage(c, cls) {
  const key = `${state.current}/${c.id}/${cls}`;
  if (!imgCache.has(key)) {
    const img = el("img", {
      class: cls, alt: "", decoding: "async", loading: cls === "thumb" ? "lazy" : null, width: c.width, height: c.height,
      src: `${API}/captures/${encodeURIComponent(state.detail.name)}/${encodeURIComponent(c.id)}`,
    });
    if (cls === "full") {
      const current = state.current;
      img.addEventListener("load", () => { if (current === state.current) scheduleRender(); }, { once: true });
      img.addEventListener("error", () => { if (current === state.current) { failedShots.add(c.id); scheduleRender(); } }, { once: true });
    }
    imgCache.set(key, img);
    const peers = [...imgCache.keys()].filter((k) => k.endsWith(`/${cls}`));
    while (peers.length > IMAGE_LIMITS[cls]) imgCache.delete(peers.shift());
  }
  return imgCache.get(key);
}
const loaded = (img) => img.complete && img.naturalWidth > 0;
function scheduleRender() {
  if (state.timers.render) return;
  state.timers.render = setTimeout(() => { state.timers.render = null; render(); }, 0);
}

// -- live moments ------------------------------------------------------------------------------

// Compares a live poll with the previous one and queues the real events that finished in between
// (a recovery, a safe stop, a scenario completed), so each one is visible for a moment.
function noticeLive(key) {
  const b = bundle();
  // The final poll can already be "saved": still present events observed since the live poll.
  if (!b || (!isLive() && !Object.keys(state.seen).some((id) => id.startsWith(`${key}|`)))) { state.seen = {}; return; }
  b.scenarios.forEach((sc) => {
    const id = `${key}|${sc.key}`;
    const before = state.seen[id];
    state.seen[id] = { state: sc.state, steps: Object.fromEntries(sc.steps.map((s) => [s.id, `${s.state}/${s.phase || ""}`])) };
    if (!before || state.userPickedScenario) return;
    sc.steps.forEach((s, i) => {
      const was = before.steps[s.id] || "";
      if (!/^(pending|running)\//.test(was)) return;
      if (s.state === "recovered") {
        if (!was.endsWith("/recovering")) addMoment(sc.key, i, "investigating"); // recovery_started happened
        addMoment(sc.key, i, "proud");
      } else if (s.state === "failed") {
        addMoment(sc.key, i, s.recovery && ["rejected", "error"].includes(s.recovery.outcome) ? "cautious" : "alert");
      }
    });
    if (before.state === "running" && sc.state === "finished" && sc.status === "passed") {
      addMoment(sc.key, sc.steps.length - 1, recoveredSteps(sc).length ? "proud" : "happy");
    }
  });
}

function addMoment(sc, idx, mood) {
  if (state.moments.length >= MAX_MOMENTS) return; // bounded: the console never lags far behind
  state.moments.push({ sc, idx, mood });
  if (!state.moment) nextMoment();
}

function nextMoment() {
  clearTimeout(state.timers.moment);
  state.moment = state.moments.shift() || null;
  if (state.moment) {
    if (!state.userPickedScenario) state.scenarioKey = state.moment.sc;
    state.timers.moment = setTimeout(() => { nextMoment(); render(); }, MOMENT_MS);
  }
}

function clearMoments() {
  clearTimeout(state.timers.moment);
  state.moments = [];
  state.moment = null;
  state.seen = {};
}

// The capture shown for a step: the one chosen in the filmstrip, else the latest real screenshot
// taken at or before it (captures are listed in the order they were taken; never a later one).
function captureFor(sc, idx) {
  const order = Object.fromEntries(sc.steps.map((s, i) => [s.id, i]));
  // The final page belongs to the end of a finished run, shown when the last step is in focus.
  const finalOk = sc.state === "finished" && replayDone(sc) && idx === sc.steps.length - 1;
  const pos = (c) => (c.step_id === null || c.step_id === undefined ? (finalOk ? END_OF_RUN : -1) : order[c.step_id]);
  const usable = shotsOf(sc).filter((c) => {
    const at = pos(c);
    return at !== undefined && at >= 0 && (at <= idx || at === END_OF_RUN) && reachedCapture(sc, at);
  });
  const chosen = usable.find((c) => c.id === state.shot && (c.step_id ? order[c.step_id] === idx : true));
  if (chosen && !failedShots.has(chosen.id)) return chosen;
  const newest = usable.map((c, n) => [pos(c), n, c]).sort((a, b) => a[0] - b[0] || a[1] - b[1]).map((x) => x[2]).reverse();
  if (!newest.length) return null;
  // Live: show the newest image that has already loaded (no blank frame while the next one
  // arrives); it is swapped in as soon as it is ready.
  const healthy = newest.filter((c) => !failedShots.has(c.id));
  if (!healthy.length) return null;
  const next = healthy[0];
  captureImage(next, "full"); // preload only the next frame; never every earlier full-size image
  if (isLive()) return healthy.find((c) => {
    const img = imgCache.get(`${state.current}/${c.id}/full`);
    return img && loaded(img);
  }) || next;
  return next;
}
// During a replay only captures of steps the cursor already reached are shown.
function reachedCapture(sc, at) {
  if (!replaying(sc)) return true;
  return at !== END_OF_RUN && at <= state.replay.index;
}

function renderCapture(sc, steps, idx) {
  const shots = shotsOf(sc);
  const available = shots.length > 0;
  $("view-toggle").hidden = !available;
  $("show-capture").setAttribute("aria-pressed", String(state.stageView === "capture"));
  $("show-reconstructed").setAttribute("aria-pressed", String(state.stageView !== "capture"));
  const c = available && state.stageView === "capture" ? captureFor(sc, idx) : null;
  const step = steps[idx];
  const skippedHere = (sc.captures || []).find((x) => x.skipped && x.step_id === (step && step.id));
  $("shot").hidden = !c;
  $("page").hidden = !!c;
  fill($("stage-prov"), c ? badge(isLive() ? "live" : "recorded", isLive() ? "Live capture" : "Recorded capture", isLive())
    : badge("none", "Reconstructed view"));
  renderFilm(sc, c);
  if (!c) return false;

  const at = sc.steps.findIndex((s) => s.id === c.step_id);
  const r = step && step.recovery;
  const own = step && step.id === c.step_id;
  const investigating = momentOf(sc) && momentOf(sc).mood === "investigating";
  const tag = c.phase === "not_found"
    ? (own && step.state === "recovered" && r && r.proposed && !investigating
      ? `Recorded element not found here · GhostFlow then chose "${r.proposed.name || r.proposed.id}"`
      : own && step.state === "failed" ? "Recorded element not found here · stopped safely without acting"
        : "Recorded element not found on this page")
    : c.phase === "final" ? (sc.status === "passed" ? "Final page after the verifications" : reviewIssue(sc) || "Page where the run stopped safely") : null;
  if (c.width && c.height) {
    $("shot-frame").style.setProperty("--shot-w", `${c.width}px`);
    $("shot-frame").style.setProperty("--shot-ratio", String(c.width / c.height));
  }
  fill($("shot-frame"),
    captureImage(c, "full"),
    c.box && step && c.step_id === step.id ? el("span", {
      class: `focus-box ${c.phase}`, "aria-hidden": "true",
    }) : null);
  const hl = $("shot-frame").querySelector(".focus-box");
  if (hl) {
    const pct = (v) => `${(v * 100).toFixed(2)}%`;
    hl.style.left = pct(c.box.x); hl.style.top = pct(c.box.y);
    hl.style.width = pct(c.box.w); hl.style.height = pct(c.box.h);
  }
  const ownShot = !step || c.step_id === step.id || c.phase === "final";
  fill($("shot-note"),
    tag ? el("span", { class: `shot-tag ${c.phase}${sc.status !== "passed" ? " unverified" : ""}` }, tag) : null,
    el("strong", {}, c.step_id ? `Step ${c.step_id}` : "Final page"), ` · ${{ before: "just before acting", after: "page loaded", not_found: "element missing", final: "after the verifications" }[c.phase]}`,
    ` · ${c.taken_at.replace("T", " ").slice(11, 19)} UTC`,
    c.masked ? ` · ${plural(c.masked, "field")} masked while capturing` : "",
    !ownShot ? el("span", { class: "muted" }, ` · latest capture before ${step.id}${skippedHere ? `: ${SKIP_TEXT[skippedHere.skipped]}` : ""}`) : "");
  $("shot-frame").setAttribute("aria-label", `Screenshot of ${c.page || "the page"}${at >= 0 ? ` at step ${c.step_id}` : ""}`);
  $("shot-frame").setAttribute("role", "img");
  $("address").textContent = c.page || "—";
  return true;
}

function renderFilm(sc, current) {
  const shots = shotsOf(sc).filter((c) => {
    const at = c.step_id ? sc.steps.findIndex((s) => s.id === c.step_id) : END_OF_RUN;
    return reachedCapture(sc, at);
  });
  $("film").hidden = !shots.length || state.stageView !== "capture";
  fill($("film"), shots.map((c) => {
    const b = el("button", { type: "button", class: `frame${current && current.id === c.id ? " on" : ""}`, "aria-label": `Capture of ${c.step_id || "the final page"} (${c.phase.replace("_", " ")})`, "aria-current": current && current.id === c.id ? "true" : null, "data-focus-key": `capture-${c.id}` },
      captureImage(c, "thumb"), el("span", {}, c.step_id || "end"));
    b.addEventListener("click", () => {
      stopReplay();
      clearMoments();
      state.pinned = c.step_id ? sc.steps.findIndex((s) => s.id === c.step_id) : sc.steps.length - 1;
      state.shot = c.id;
      render();
    });
    return el("li", {}, b);
  }));
}

// Recovery, beside the browser: what was missing, what GhostFlow chose, whether its checks passed
// and where the proposal came from. Candidates, latency, tokens and hashes stay in Developer.
function renderHealSummary() {
  const sc = scenario();
  const box = $("heal-summary");
  const title = el("h2", { id: "heal-summary-title" }, "Recovery");
  const head = (cls, text) => el("div", { class: "summary-head" }, title, el("span", { class: `status-chip ${cls}` }, text));
  if (!sc) return fill(box, el("div", { class: "summary-head" }, title));
  const target = healingStep(sc);
  const steps = visibleSteps(sc);
  const idx = target ? sc.steps.indexOf(target) : -1;
  const reached = target && reachedStep(sc, idx);
  const stage = recoveryStage(bundle(), sc);
  const fact = (k, ...v) => [el("dt", {}, k), el("dd", {}, ...v)];
  const facts = (...rows) => el("dl", { class: "facts" }, rows.flat());
  const source = () => fact("Source", stage.source);

  if (!target) {
    const [cls, text, msg] = sc.status === "error" ? ["fail", "Not reached", "The run stopped before a recovery was relevant."]
      : sc.state !== "finished" ? (sc.healing ? ["muted", "Standing by", "GhostFlow recovers only if a recorded element goes missing."] : ["muted", "Self-healing off", "Self-healing is off in this scenario."])
        : ["ok", "Not needed", "No recovery needed: every recorded element was found."];
    return fill(box, head(cls, text), el("p", { class: "explain" }, msg), sc.state !== "finished" && sc.healing ? facts(source()) : null);
  }
  const original = fact("Original", el("s", {}, elementName(target.target || target.label)), el("span", { class: "miss" }, " · not found"));
  const r = target.recovery;
  if (!r || (steps[idx].phase && !reached)) {
    return fill(box, head("active", "In progress"), facts(original,
      fact("Selected", el("span", { class: "muted" }, steps[idx].phase === "evaluating" ? "Playback of the recorded checks" : "Searching for an equivalent…")), source()));
  }
  if (!reached) return fill(box, head("muted", "Replay"), el("p", { class: "explain" }, `Replay in progress · step ${target.id} not reached yet.`), facts(source()));

  const recovered = r.outcome === "recovered";
  const passed = r.validation.filter((c) => c.passed).length;
  const status = recovered ? ["ok", "Recovered"] : r.outcome === "skipped" ? ["muted", sc.healing ? "Not attempted" : "Self-healing off"]
    : r.outcome === "error" ? ["fail", "Error"] : ["fail", `${r.outcome[0].toUpperCase()}${r.outcome.slice(1)} · stopped`];
  // What this panel shows is the selected evidence, not the current backend settings in the header.
  const details = el("button", { type: "button", class: "link-btn", "aria-label": "View the recovery details in the Developer view" }, "View details");
  details.addEventListener("click", () => setView("developer"));
  fill(box, head(...status),
    facts(original,
      recovered && r.proposed ? fact("Selected", el("strong", { class: "new" }, r.proposed.name || r.proposed.id))
        : fact("Result", r.outcome === "skipped" ? "Not replaced · GhostFlow did not guess" : "Not applied · GhostFlow did not act"),
      r.validation.length ? fact("Checks", el("span", { class: passed === r.validation.length ? "ok-text" : "fail-text", title: "GhostFlow's deterministic checks, run before acting" }, `${passed}/${r.validation.length} passed`)) : [],
      source()),
    el("div", { class: "heal-foot" }, el("span", {}, isLive() ? "Live recovery · this run" : "Recorded recovery · selected evidence"), details));
}

// Interpretation (GF-002) is optional context for self-healing (GF-003), never a requirement.
function independenceNote(b) {
  const healed = b.scenarios.some((sc) => sc.steps.some((s) => s.recovery && s.recovery.consulted_model));
  return healed ? el("p", { class: "provenance" }, "The recorded recovery (GF-003) is independent of the interpretation (GF-002): it ran on this same workflow without one, using the recorded step, the page's real candidates and GhostFlow's deterministic checks.") : null;
}

// -- AI provenance, stage by stage ------------------------------------------------------------
// The interpretation (GF-002) and the recovery (GF-003) are separate model calls. Each stage is
// labelled from its own record, never from a global "AI" setting, and request counts are shown
// only when the evidence stores them. `chip` is the compact Demo label; `lines` are the details.

const advisorName = (ai) => (ai.provider === "meta" ? `Meta Muse Spark · ${ai.model} (real requests)`
  : ai.provider === "mock" ? "the offline mock (simulated) · 0 Meta requests" : "none configured");

function interpretationStage(b) {
  const i = b.workflow.interpretation;
  if (!i) return { kind: "none", tag: "None", chip: "Interpretation · None", title: "No interpretation stored", lines: [`${b.workflow.source}.`] };
  const when = `generated ${utc(i.generated_at)} when the workflow was compiled (GF-002)`;
  if (i.provider === "meta") {
    return { kind: "recorded-real", tag: "Real · Meta", chip: "Interpretation · Meta recorded", title: `Meta Muse Spark · ${i.model}`,
      lines: [`Real model call, recorded: ${when}.`, "Stored with the workflow: not a new request and not part of the run shown here."] };
  }
  return { kind: "simulated", tag: "Simulated", chip: `Interpretation · Simulated (${i.provider})`, title: `Simulated interpretation · ${i.provider} (${i.model})`, lines: [`Not generated by Meta: ${when}.`] };
}

function recoveryStage(b, sc) {
  const mode = state.detail.presentation.mode;
  const target = healingStep(sc);
  const idx = target ? sc.steps.indexOf(target) : -1;
  const r = target && target.recovery;
  const none = (chip, ...lines) => ({ kind: "none", tag: "None", chip, source: "None", title: "No AI intervention", lines });
  const advisor = b.ai.provider === "meta" ? "Meta" : b.ai.provider === "mock" ? "Mock" : "no advisor";
  const advisorSource = b.ai.provider === "meta" ? `Meta Muse Spark · ${b.ai.model}` : b.ai.provider === "mock" ? "Mock · simulated" : "None";
  if (!target) {
    if (sc.state === "finished" && sc.status === "passed") return none("Recovery · Not needed", "Every recorded element was found; the model was not consulted.");
    if (sc.state === "finished") return none("Recovery · None", "No step needed a recovery; the model was not consulted.");
    return sc.healing
      ? { kind: b.ai.provider === "mock" ? "simulated" : "none", tag: "Standing by", chip: `Recovery · ${advisor} · standing by`, source: advisorSource, title: "No intervention so far",
        lines: [`Self-healing is on. Advisor if a recorded element goes missing: ${advisorName(b.ai)}.`] }
      : none("Recovery · Off", `Self-healing is off in scenario ${sc.key}: no model is consulted.`);
  }
  if (!r) {
    return { kind: b.ai.provider === "mock" ? "simulated" : "none", tag: "In progress", chip: `Recovery · ${advisor} · in progress`, source: advisorSource, title: "Recovery in progress",
      lines: [`Advisor for this run: ${advisorName(b.ai)}.`] };
  }
  const real = r.provider === "meta";
  const who = real ? `Meta Muse Spark · ${r.model}` : "the offline mock (simulated)";
  if (r.consulted_model && !reachedStep(sc, idx)) {
    return { kind: real ? "recorded-real" : "simulated", tag: "Playback", chip: `Recovery · ${real ? "Meta real" : "Mock"} · playback`,
      source: real ? "Meta Muse Spark · recorded" : "Mock · simulated", title: `Recorded recovery by ${who}`, lines: [`The recorded result appears when the replay reaches ${target.id}.`] };
  }
  if (!r.consulted_model) return none(sc.healing ? "Recovery · Not attempted" : "Recovery · Off", `${r.reason || "Self-healing was not attempted"}. The model was not consulted.`);
  const passed = r.validation.filter((c) => c.passed).length;
  const outcome = `Outcome: ${r.outcome}${r.validation.length ? ` · ${passed}/${r.validation.length} deterministic checks passed` : ""}`
    + `${r.outcome === "recovered" && r.proposed ? ` · chose "${r.proposed.name || r.proposed.id}" for ${elementName(target.target || target.label)}` : ""}.`;
  if (real) {
    const n = b.ai.provider === "meta" ? b.ai.requests_sent : 0;
    // Real Meta in this live run, or a real call recorded earlier (never presented as a new one).
    const when = mode === "live" ? "live" : "recorded";
    return { kind: mode === "live" ? "real" : "recorded-real", tag: "Real · Meta", chip: `Recovery · Meta real · ${when}`,
      source: `Meta Muse Spark · ${when}${n > 0 ? ` · ${plural(n, "request")}` : ""}`, title: `Meta Muse Spark · ${r.model}`, lines: [
        mode === "live" ? "Real model call made during this live run."
          : mode === "recorded" ? "Real model call, recorded evidence: shown from the file, not a new request."
            : "Real model call made during this saved run; viewing it sends no new request.",
        n > 0 ? `${plural(n, "real Meta request")} recorded in this evidence${b.ai.request_cap ? ` (cap ${b.ai.request_cap})` : ""}.` : "The number of requests is not recorded in this evidence.",
        outcome] };
  }
  const mock = r.provider === "mock";
  return { kind: "simulated", tag: "Simulated", chip: mock ? "Recovery · Mock" : `Recovery · Simulated (${r.provider})`,
    source: mock ? `Mock · simulated${b.ai.provider === "meta" ? "" : " · 0 Meta requests"}` : `Simulated · ${r.provider}`,
    title: mock ? "Offline mock · simulated recovery" : `Simulated recovery · ${r.provider}`, lines: [
      `The advisor's answer was simulated by ${r.model}. ${b.ai.provider === "meta" ? "This step was not answered by Meta." : "0 Meta requests in this evidence."}`,
      b.origin === "demo_runner" ? "The browser run itself is real: Playwright drove Chromium through the portal." : null,
      outcome].filter(Boolean) };
}

function provenanceNote(b, sc) {
  const i = b.workflow.interpretation;
  const target = healingStep(sc);
  const r = target && target.recovery;
  const consulted = !!(r && r.consulted_model);
  const ran = sc.started_at ? `on ${utc(sc.started_at)}` : "in this evidence";
  if (i && consulted) {
    // A mock answer is not a model call: name each stage by its own source.
    const stored = `${i.provider === "meta" ? "the real Meta interpretation" : `the interpretation simulated by ${i.provider}`} was stored ${utc(i.generated_at)} when the workflow was compiled`;
    if (r.provider !== "meta") {
      return `Two independent AI stages, not one execution: ${stored}; the recovery ${ran} was simulated by the offline mock.`
        + `${i.provider === "meta" ? " Neither stage sent a new Meta request." : " No Meta request was made."}`;
    }
    return `Two independent AI stages, not one execution: ${stored}; the recovery by Meta Muse Spark ran ${ran}.`;
  }
  if (i) return `The interpretation was stored ${utc(i.generated_at)} when the workflow was compiled; scenario ${sc.key} ran ${ran}, separately.`;
  if (consulted) return "No interpretation is stored for this workflow: the recovery ran without one (GF-003 does not depend on GF-002).";
  return "";
}

function stageBlock(n, label, s) {
  return el("div", { class: `prov-stage ${s.kind}` },
    el("div", { class: "ps-head" }, el("span", { class: "ps-n", "aria-hidden": "true" }, n), el("h3", {}, label), badge(s.kind, s.tag)),
    el("p", { class: "ps-title" }, s.title),
    el("ul", { class: "ps-lines" }, s.lines.map((t) => el("li", {}, t))));
}

const chipFor = (stage) => badge(stage.kind, stage.chip);

// Demo view: one compact chip per AI stage, for the selected scenario. Details are in Developer.
function renderProvRow() {
  const b = bundle();
  const sc = scenario();
  fill($("prov-row"), sc ? [interpretationStage(b), recoveryStage(b, sc)].map((st) => el("li", {}, chipFor(st))) : []);
}

// Developer view (and the expanded dialog): every stage with its dates, model and requests.
function renderProvenance() {
  const b = bundle();
  const sc = scenario();
  if (!sc) { fill($("prov-stages")); fill($("xb-prov")); $("prov-note").textContent = ""; return; }
  const blocks = () => [stageBlock("1", "Workflow interpretation", interpretationStage(b)), stageBlock("2", `Self-Healing recovery · scenario ${sc.key}`, recoveryStage(b, sc))];
  fill($("prov-stages"), blocks());
  fill($("xb-prov"), blocks());
  $("prov-note").textContent = provenanceNote(b, sc);
}

// -- expanded browser ------------------------------------------------------------------------
// The viewer's own nodes move into a modal dialog and back: live captures, replay, filmstrip and
// labels stay in sync because nothing is copied or rendered twice. Images keep their own size.

const moved = []; // [placeholder, node], restored in reverse order
function moveInto(node, target) {
  const mark = document.createComment(node.id);
  node.before(mark);
  moved.push([mark, node]);
  target.append(node);
}

function openExpanded() {
  if (state.expanded || !scenario()) return;
  state.expanded = true;
  moveInto($("stage-col"), $("xb-main"));
  moveInto($("events"), $("xb-events"));
  moveInto($("film"), $("xb-film")); // thumbnails beside the image leave it more height
  $("stage-away").hidden = false;
  document.documentElement.classList.add("xb-open");
  const dialog = $("browser-dialog");
  if (typeof dialog.showModal === "function") dialog.showModal(); else dialog.setAttribute("open", "");
  render();
  $("xb-close").focus();
}

function exitExpanded() {
  const dialog = $("browser-dialog");
  if (typeof dialog.close === "function" && dialog.open) dialog.close(); // fires "close"
  else { dialog.removeAttribute("open"); restoreLayout(); }
}

// The dialog closed (button, Escape): the viewer goes back where it was and focus returns.
function restoreLayout() {
  if (!state.expanded) return;
  state.expanded = false;
  while (moved.length) { const [mark, node] = moved.pop(); mark.replaceWith(node); }
  $("stage-away").hidden = true;
  document.documentElement.classList.remove("xb-open");
  render();
  $("expand-browser").focus();
}

function renderExpanded() {
  $("browser-dialog").classList.toggle("waiting", !state.detail);
  if (!state.expanded) return;
  const sc = state.detail && scenario();
  if (!sc || !state.at) {
    $("xb-title").textContent = "Expanded browser";
    fill($("xb-progress"), "Loading the evidence…");
    return;
  }
  const steps = visibleSteps(sc);
  const idx = Math.min(state.at.idx, steps.length - 1);
  const step = steps[idx];
  $("xb-title").textContent = `Scenario ${sc.key} — ${shortTitle(sc)}`;
  // The replay banner and the source badges stay behind the modal: repeat what kind of evidence this is.
  const mode = state.detail.presentation;
  fill($("xb-progress"),
    replaying(sc) ? badge("stale", "Replay of saved evidence · playback pace") : badge(mode.mode, mode.text.split(" · ")[0], mode.mode === "live"),
    " ", el("strong", {}, `Step ${idx + 1} of ${steps.length}`), ` · ${step.id} · ${STATE_TEXT[step.state]}`,
    el("span", { class: "muted" }, ` · scenario: ${scenarioOutcome(sc).text}`));
  fill($("xb-dots"), steps.map((s, i) => el("li", { class: `${s.state}${i === idx ? " on" : ""}` })));
  $("xb-prev").setAttribute("aria-disabled", String(idx === 0));
  $("xb-next").setAttribute("aria-disabled", String(idx >= steps.length - 1));
  $("xb-agent").setAttribute("data-mood", $("mascot").getAttribute("data-mood"));
  $("xb-agent-label").textContent = $("mascot-say").textContent;
}

// -- developer view ------------------------------------------------------------------------

function renderTimeline() {
  const sc = scenario();
  if (!sc) return fill($("timeline"));
  const steps = visibleSteps(sc);
  const runError = sc.status === "error" && sc.error ? el("li", { class: "step failed" }, el("span", { class: "mark", "aria-hidden": "true" }, "!"), el("div", { class: "body" }, el("div", { class: "label" }, "Execution error"), el("div", { class: "err" }, sc.error)), el("div", {})) : null;
  fill($("timeline"), runError, steps.map((s) => {
    const chips = [];
    if (s.phase === "recovering" || s.phase === "evaluating") chips.push(el("span", { class: "chip rec" }, "Recovery in progress"));
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
  const reached = target && reachedStep(sc, idx);
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
  const inProgress = !r || (showing && showing.phase && !reached);
  if (inProgress) {
    const provider = r ? r.provider : (bundle().ai.provider === "meta" ? "meta" : "mock");
    fill(outcomeBox, el("span", { class: "chip rec" }, "In progress"));
    return fill(box, el("p", { class: "note strong" }, `Step ${target.id}: ${elementName(target.target || target.label)} is gone from the page.`),
      el("h3", {}, "Recorded locators that failed"), el("ul", { class: "strategies" }, (r ? r.exhausted_strategies : target.exhausted_strategies || []).map((x) => el("li", {}, x))),
      el("p", { class: "note" }, `Recovery in progress (advisor: ${provider === "meta" ? "Meta Muse Spark" : "the offline mock, simulated"}). The result appears when it finishes.`));
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
    : r.outcome === "error" ? "Recovery error: review the outcome before retrying."
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

function captureSummary(sc) {
  const all = sc.captures || [];
  if (!all.length) return state.detail.collection === "recorded" ? "None: this recorded evidence stored no images" : "None in this evidence";
  const shots = all.filter((c) => !c.skipped);
  const bytes = shots.reduce((t, c) => t + (c.bytes || 0), 0);
  const skipped = {};
  all.filter((c) => c.skipped).forEach((c) => { skipped[c.skipped] = (skipped[c.skipped] || 0) + 1; });
  const why = Object.entries(skipped).map(([k, n]) => `${n} ${k.replace("_", " ")}`).join(", ");
  return `${shots.length} real screenshots (${(bytes / 1048576).toFixed(1)} MB)${why ? ` · not captured: ${why}` : ""}`;
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
      el("dt", {}, "Blocked requests"), el("dd", {}, String(sc.blocked_requests.length)),
      el("dt", {}, "Browser captures"), el("dd", {}, captureSummary(sc))),
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
  document.body.dataset.view = dev ? "developer" : "demo"; // the backend settings pills only show in Developer
  $("demo-view").hidden = dev;
  $("dev-view").hidden = !dev;
  for (const [id, on] of [["tab-demo", !dev], ["tab-dev", dev]]) {
    $(id).setAttribute("aria-selected", on ? "true" : "false");
    $(id).setAttribute("tabindex", on ? "0" : "-1");
  }
}

function render() {
  const focusKey = document.activeElement?.dataset.focusKey;
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
    renderExpanded();
    return;
  }
  renderScenarios();
  renderStage();
  renderHero();
  renderProvenance();
  renderHealSummary();
  renderTimeline();
  renderHealing();
  renderIntelligence();
  renderEvidence();
  renderExpanded();
  const b = bundle();
  $("source-hint").textContent = `Source: ${b.workflow.source}. ${b.provenance || ""}`;
  if (focusKey) document.querySelector(`[data-focus-key="${CSS.escape(focusKey)}"]`)?.focus({ preventScroll: true });
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
  state.shot = null;
  state.replay = { key: sc.key, index: 0, stage: 0 };
  const tick = () => {
    const current = scenario();
    if (!state.replay || !current) return;
    const here = current.steps[state.replay.index];
    if (here && here.recovery && here.recovery.consulted_model && state.replay.stage < 2) {
      state.replay.stage += 1; // investigating -> evaluating -> recorded result
    } else {
      state.replay.index += 1;
      state.replay.stage = 0;
    }
    if (state.replay.index > current.steps.length) { stopReplay(); render(); return; }
    render();
    const next = current.steps[state.replay.index];
    const staged = next && next.recovery && next.recovery.consulted_model;
    state.timers.replay = setTimeout(tick, staged ? REPLAY_STAGE_MS : REPLAY_STEP_MS);
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
  if (live && state.current && $("follow").checked && keyOf(live) !== state.current) return select(keyOf(live));
  if (!state.current) {
    const first = state.sources.find((s) => s.valid && s.collection === "runs")
      || state.sources.find((s) => s.valid && s.collection === "recorded")
      || state.sources.find((s) => s.valid);
    const fromHash = evidenceHash();
    const wanted = state.sources.find((s) => keyOf(s) === fromHash && s.valid) || first;
    if (wanted) return select(keyOf(wanted));
  }
  // A slow advisor can outlast LIVE_WINDOW. Sources polling must restart detail polling when
  // this same run resumes or finishes; choosing a new source is not required to see its result.
  const listed = state.sources.find((s) => keyOf(s) === state.current);
  if (listed?.valid && !isLive() && !state.replay && !detailRequest && (
    !state.detail || listed.collection === "workflows" ||
    (listed.presentation && listed.presentation.mode !== state.detail.presentation.mode) ||
    Date.parse(listed.updated_at) > Date.parse(state.detail.bundle.updated_at)
  )) return loadDetail();
  if (!state.replay) render(); else { renderSystem(); renderSources(); }
}

async function loadDetail() {
  if (!state.current) return;
  clearTimeout(state.timers.live);
  if (detailRequest) detailRequest.abort();
  detailRequest = new AbortController();
  const version = ++detailVersion;
  const key = state.current;
  const [collection, ...rest] = key.split("/");
  let detail = null, error = null;
  try {
    detail = await getJSON(`${API}/sources/${encodeURIComponent(collection)}/${encodeURIComponent(rest.join("/"))}`, detailRequest.signal);
  } catch (err) {
    if (err.name === "AbortError") return;
    error = `This evidence cannot be shown (${err.message}).`;
  }
  if (key !== state.current) return; // another source was chosen meanwhile: never show a stale answer
  if (version !== detailVersion) return; // A → B → A also invalidates the first A request
  detailRequest = null;
  state.detail = detail;
  state.error = error;
  noticeLive(key);
  const b = bundle();
  if (b && state.moment && !state.userPickedScenario) {
    state.scenarioKey = state.moment.sc;
  } else if (b && !state.userPickedScenario) {
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
  clearTimeout(state.timers.live);
  imgCache.clear();
  failedShots.clear();
  state.current = key;
  state.detail = null;
  state.userPickedScenario = false;
  state.scenarioKey = null;
  state.pinned = null;
  state.shot = null;
  state.stageView = "capture"; // each evidence starts on its real captures, if it has any
  clearMoments();
  const url = new URL(location.href);
  url.hash = encodeURIComponent(key);
  history.replaceState(null, "", url);
  render();
  await loadDetail();
}

function evidenceHash() {
  try {
    const key = decodeURIComponent(location.hash.slice(1));
    return /^(recorded|runs|workflows)\/[^/]+$/.test(key) ? key : "";
  } catch (_) { return ""; }
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
  $("source-select").addEventListener("change", (e) => { $("follow").checked = false; select(e.target.value); });
  $("replay").addEventListener("click", () => { if (state.replay) { stopReplay(); render(); } else startReplay(); });
  $("tab-demo").addEventListener("click", () => setView("demo"));
  $("tab-dev").addEventListener("click", () => setView("developer"));
  $("show-capture").addEventListener("click", () => { state.stageView = "capture"; render(); });
  $("show-reconstructed").addEventListener("click", () => { state.stageView = "reconstructed"; render(); });
  $("expand-browser").addEventListener("click", openExpanded);
  $("xb-close").addEventListener("click", exitExpanded);
  $("browser-dialog").addEventListener("close", restoreLayout); // Escape or the close button
  for (const [id, delta] of [["xb-prev", -1], ["xb-next", 1]]) {
    $(id).addEventListener("click", () => { if ($(id).getAttribute("aria-disabled") !== "true") stepBy(delta); });
  }
  $("browser-dialog").addEventListener("keydown", (e) => {
    if (e.key === "Escape") { e.preventDefault(); exitExpanded(); return; }
    if (e.altKey || e.ctrlKey || e.metaKey || !["ArrowLeft", "ArrowRight"].includes(e.key)) return;
    e.preventDefault();
    stepBy(e.key === "ArrowLeft" ? -1 : 1);
  });
  arrowTabs(document.querySelector(".segmented"), (tab) => setView(tab.id === "tab-dev" ? "developer" : "demo"));
  arrowTabs($("scenarios"), (tab) => { chooseScenario(tab.getAttribute("data-key")); const again = $("scenarios").querySelector('[aria-selected="true"]'); if (again) again.focus(); });
  // The address names the evidence (#collection%2Fname): following a link or editing it switches source.
  window.addEventListener("hashchange", () => {
    const key = evidenceHash();
    if (key && key !== state.current) { $("follow").checked = false; select(key); }
  });
  loadSources();
  setInterval(loadSources, SOURCES_POLL_MS);
});
