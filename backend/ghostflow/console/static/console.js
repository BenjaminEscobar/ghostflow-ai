// GhostFlow Demo Console. Read-only: it only GETs /api/demo/* and renders with textContent
// (never HTML parsing), so nothing in an evidence file can become markup or code.
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

const state = {
  sources: [],
  backend: null,
  current: null, // "collection/name"
  detail: null,
  scenarioKey: null,
  userPickedScenario: false,
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
const badge = (kind, text, dot) => el("span", { class: `badge ${kind}` }, dot ? el("span", { class: "dot" }) : null, text);

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

function scenarioOutcome(sc) {
  if (state.replay && state.replay.key === sc.key) return { cls: "running", text: "Replaying saved evidence…" };
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
  if (!state.replay || state.replay.key !== sc.key) return sc.steps;
  const cursor = state.replay.index;
  return sc.steps.map((s, i) => {
    if (i < cursor) return s;
    if (i === cursor) return { ...s, state: s.state === "skipped" ? "skipped" : "running", phase: s.recovery && s.recovery.consulted_model ? "recovering" : null, error: null };
    return { ...s, state: "pending", phase: null, error: null };
  });
}
const replayDone = (sc) => !state.replay || state.replay.key !== sc.key;

// -- rendering ---------------------------------------------------------------------------

function renderSources() {
  const select = $("source-select");
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
  fill(select, children);
}

function renderBadges() {
  const d = state.detail;
  if (!d) return fill($("badges"));
  const p = d.presentation;
  fill($("badges"),
    badge(p.mode, p.text, p.mode === "live"),
    badge(d.ai_badge.kind, d.ai_badge.text));
}

function stat(k, v, s, cls) {
  return el("div", { class: "stat" }, el("div", { class: "k" }, k), el("div", { class: `v ${cls || ""}` }, v), s ? el("div", { class: "s" }, s) : null);
}

function renderHero() {
  const b = bundle();
  const sc = scenario();
  const w = b.workflow;
  const secrets = w.inputs.filter((i) => i.sensitive).length;
  const outcome = sc ? scenarioOutcome(sc) : { cls: "", text: "—" };
  const verif = sc && replayDone(sc) ? sc.verifications : [];
  const passed = verif.filter((v) => v.status === "passed").length;
  const ai = b.ai.provider === "none" ? "None" : b.ai.provider === "meta" ? "Meta" : "Mock";
  const params = w.inputs.map((i) => i.label || i.name).join(", ");
  fill($("hero"),
    el("div", { class: "hero-title" },
      el("div", { class: "kicker" }, "Learned workflow"),
      el("h1", {}, w.name),
      el("p", {}, w.description || "Show GhostFlow a workflow once. It learns, understands, executes, and safely adapts when the interface changes.")),
    el("div", { class: "stats" },
      stat("Steps", String(w.steps.length), w.allowed_origins.join(", ")),
      stat("Parameters", String(w.inputs.length), secrets ? `${params} · ${secrets} secret, never stored` : params),
      stat("Status", outcome.text, sc ? sc.title : null, { passed: "ok", failed: "fail", error: "fail", recovered: "heal", running: "run" }[outcome.cls]),
      stat("AI provider", ai, sc && !sc.healing ? "not used: self-healing off here" : b.ai.model || "—", b.ai.provider === "meta" ? "heal" : ""),
      stat("Verifications", verif.length ? `${passed}/${verif.length}` : "—",
        !verif.length ? "not run yet" : passed === verif.length ? "all passed" : verif.some((v) => v.status === "failed") ? "failed" : "skipped: the run stopped earlier",
        verif.length && passed === verif.length ? "ok" : verif.length ? "fail" : "")));
}

function renderScenarios() {
  const b = bundle();
  fill($("scenarios"), b.scenarios.map((sc) => {
    const o = scenarioOutcome(sc);
    const btn = el("button", { type: "button", class: `scenario ${o.cls}`, role: "tab", "aria-selected": sc.key === (scenario() || {}).key ? "true" : "false" },
      el("span", { class: "key" }, `SCENARIO ${sc.key}`), el("span", { class: "title" }, sc.title.replace(/^[A-Z]\d* · /, "")), el("span", { class: "st" }, o.text));
    btn.addEventListener("click", () => { stopReplay(); state.scenarioKey = sc.key; state.userPickedScenario = true; render(); });
    return btn;
  }));
}

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
        el("div", { class: "meta" }, `${s.id} · ${s.action}${s.duration_ms != null && s.state !== "pending" ? ` · ${s.duration_ms} ms` : ""} · ${STATE_TEXT[s.state]}`),
        s.state === "failed" && s.error ? el("div", { class: "err" }, s.error) : null),
      el("div", {}, chips));
  }));
  const b = bundle();
  $("replay").hidden = !(state.detail.presentation.mode !== "live" && sc.state === "finished");
  $("replay").textContent = state.replay ? "■ Stop replay" : "▶ Replay evidence";
  if (b) $("timeline-title").textContent = `Execution · scenario ${sc.key}`;
}

function elementName(description) {
  const m = /"([^"]+)"/.exec(description || "");
  return m ? m[1] : description || "element";
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
      el("h3", {}, "Recorded locators that failed"), el("ul", { class: "strategies" }, (r ? r.exhausted_strategies : target.exhausted_strategies).map((x) => el("li", {}, x))),
      el("p", { class: "note" }, `Extracting the page's real candidates and asking ${provider === "meta" ? "Meta Muse Spark" : "the offline mock (simulated)"} to pick one…`));
  }
  if (!reached) {
    return fill(box, el("p", { class: "note" }, `Replay in progress · step ${target.id} not reached yet.`));
  }

  const recovered = r.outcome === "recovered";
  fill(outcomeBox, el("span", { class: `chip ${recovered ? "ai" : r.outcome === "skipped" ? "muted" : "fail"}` }, { recovered: "Recovered", rejected: "Rejected", skipped: "Not attempted", error: "Error" }[r.outcome] || r.outcome));
  const original = el("div", { class: "el-card old" }, el("div", { class: "cap" }, `Recorded · step ${target.id}`), el("div", { class: "name" }, elementName(target.target || target.label)), el("div", { class: "role" }, target.target || target.label || ""));
  const proposed = r.proposed && recovered
    ? el("div", { class: "el-card new" }, el("div", { class: "cap" }, "Found on the new page"), el("div", { class: "name" }, r.proposed.name || r.proposed.id), el("div", { class: "role" }, `${r.proposed.role || r.proposed.kind}${r.proposed.href_path ? ` → ${r.proposed.href_path}` : ""}`))
    : el("div", { class: "el-card unknown" }, el("div", { class: "cap" }, "Equivalent element"), el("div", { class: "name" }, "Not found"), el("div", { class: "role" }, r.reason || "—"));

  const parts = [el("div", { class: "change" }, original, el("span", { class: "arrow", "aria-hidden": "true" }, "→"), proposed)];
  parts.push(el("h3", {}, `Recorded locators exhausted (${r.exhausted_strategies.length})`), el("ul", { class: "strategies" }, r.exhausted_strategies.map((x) => el("li", {}, x))));

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
      el("li", { class: c.passed ? "pass" : "nopass" }, el("span", { class: "ic" }, c.passed ? "✓" : "✕"), el("span", {}, CHECK_LABELS[c.check] || c.check), el("span", { class: "d" }, c.detail || "")))));
  }
  parts.push(el("p", { class: "authority" }, recovered
    ? `The model proposed; GhostFlow verified every rule and acted once. The workflow definition was not modified.`
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
      el("p", { class: "note" }, `${b.workflow.source}. GhostFlow shows only what a model actually returned; it never fills this panel with invented text.`));
  }
  const byId = Object.fromEntries(b.workflow.steps.map((s) => [s.id, s]));
  const critical = (i.steps || []).filter((s) => s.relevance === "critical");
  fill($("intel"),
    el("p", { class: "purpose" }, i.purpose),
    el("dl", { class: "kv" },
      el("dt", {}, "Expected outcome"), el("dd", {}, i.expected_outcome || "—"),
      el("dt", {}, "Model"), el("dd", {}, `${i.provider} · ${i.model}`),
      el("dt", {}, "Generated"), el("dd", {}, (i.generated_at || "").replace("T", " ").slice(0, 16) + " UTC")),
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
      el("dt", {}, "SHA-256"), el("dd", { class: "sha" }, b.workflow.sha256_before ? `${b.workflow.sha256_before.slice(0, 16)}…` : "—"),
      el("dt", {}, "Blocked requests"), el("dd", {}, String(sc.blocked_requests.length))),
    el("h3", {}, "Final verifications"),
    sc.verifications.length && done
      ? el("ul", { class: "plain verif" }, sc.verifications.map((v) => el("li", {}, el("span", { class: v.status === "passed" ? "ok-text" : v.status === "failed" ? "fail-text" : "muted" }, v.status === "passed" ? "✓ " : v.status === "failed" ? "✕ " : "– "), `${b.workflow.verify[v.index] || v.type}`, v.status === "skipped" ? " (not run: an earlier step failed)" : "")))
      : el("p", { class: "note" }, "Not run yet."),
    inputs.length ? [el("h3", {}, "Input data for this run"), el("dl", { class: "kv" }, inputs.flatMap(([k, v]) => [el("dt", {}, k), el("dd", {}, v)]))] : null,
    sc.derived_steps ? el("p", { class: "authority" }, "Step states before the failed or recovered step are derived from the stored summary (the executor stops at the first failure).") : null);
}

function renderFooter() {
  const b = bundle();
  $("source-hint").textContent = b ? `Source: ${b.workflow.source}. ${b.provenance || ""}` : "";
}

function render() {
  const has = !!state.detail;
  $("app").hidden = !has;
  $("empty").hidden = has;
  renderSources();
  renderBadges();
  const replaying = !!state.replay;
  $("replay-banner").hidden = !replaying;
  if (replaying) $("replay-banner").textContent = "REPLAY of saved evidence · not a live execution";
  if (!has) {
    $("empty").textContent = state.error || (state.sources.length
      ? "Choose an evidence source above."
      : "No demo evidence yet. Run `make demo-rehearsal` (offline mock) or see docs/DEMO_RUNBOOK.md. Recorded evidence lives in docs/evidence.");
    return;
  }
  renderHero();
  renderScenarios();
  renderTimeline();
  renderHealing();
  renderIntelligence();
  renderEvidence();
  renderFooter();
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
  } catch (err) {
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
  render();
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
    state.scenarioKey = (running || (state.detail.presentation.mode === "live" ? b.scenarios.filter((s) => s.state === "finished").pop() : null) || healed || b.scenarios[0] || {}).key || null;
  }
  render();
  clearTimeout(state.timers.live);
  if (state.detail && state.detail.presentation.mode === "live") state.timers.live = setTimeout(loadDetail, LIVE_POLL_MS);
}

async function select(key) {
  stopReplay();
  state.current = key;
  state.userPickedScenario = false;
  state.scenarioKey = null;
  history.replaceState(null, "", `#${encodeURIComponent(key)}`);
  await loadDetail();
}

document.addEventListener("DOMContentLoaded", () => {
  $("source-select").addEventListener("change", (e) => select(e.target.value));
  $("replay").addEventListener("click", () => { if (state.replay) { stopReplay(); render(); } else startReplay(); });
  loadSources();
  setInterval(loadSources, SOURCES_POLL_MS);
});
