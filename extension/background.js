// GhostFlow Recorder service worker: owns the recording state and uploads recordings.
// State lives in chrome.storage.session so it survives service-worker restarts but not a
// browser restart, and never syncs anywhere.
importScripts("config.js", "shared.js");

const CFG = globalThis.GHOSTFLOW_CONFIG;
const { isAllowedUrl, isSensitiveDescriptor, looksLikeSecret } = globalThis.GhostFlowShared;

const STATE_KEY = "gf_state";
const EVENT_TYPES = new Set(["navigation", "click", "input", "select", "check", "press", "observation"]);
const TARGET_KEYS = [
  "tag", "role", "accessible_name", "label", "test_id", "element_id", "name_attr",
  "placeholder", "text", "input_type", "autocomplete", "value_attr", "css"
];

function initialState() {
  return { recording: false, name: null, startedAt: null, events: [], dropped: 0, pending: null, lastUpload: null };
}

// Serialize every read-modify-write of the state.
let queue = Promise.resolve();
function withState(fn) {
  const run = queue.then(async () => {
    const state = (await chrome.storage.session.get(STATE_KEY))[STATE_KEY] || initialState();
    const result = await fn(state);
    await chrome.storage.session.set({ [STATE_KEY]: state });
    return result;
  });
  queue = run.catch(() => {});
  return run;
}

function publicState(s) {
  return {
    recording: s.recording,
    name: s.name,
    eventCount: s.events.length,
    dropped: s.dropped,
    startedAt: s.startedAt,
    hasPending: !!s.pending,
    lastUpload: s.lastUpload,
    backendUrl: CFG.backendUrl,
    allowedOrigins: CFG.allowedOrigins
  };
}

function cleanTarget(target) {
  if (!target || typeof target !== "object") return undefined;
  const out = {};
  for (const k of TARGET_KEYS) {
    if (typeof target[k] === "string" && target[k]) out[k] = target[k].slice(0, k === "css" ? 500 : 200);
  }
  return out.tag ? out : undefined;
}

// Rebuild the event from known fields only and re-apply the sensitivity rules.
function cleanEvent(raw, seq) {
  if (!raw || !EVENT_TYPES.has(raw.type) || !isAllowedUrl(raw.url)) return null;
  const ev = { seq, ts: Number(raw.ts) || Date.now(), type: raw.type, url: String(raw.url).slice(0, 2048) };
  if (typeof raw.title === "string" && raw.title) ev.title = raw.title.slice(0, 200);
  const target = cleanTarget(raw.target);
  if (target) ev.target = target;
  if (raw.type === "input" || raw.type === "select") {
    const sensitive = !!raw.sensitive || isSensitiveDescriptor(target) || looksLikeSecret(raw.value);
    ev.sensitive = sensitive;
    ev.value = sensitive || typeof raw.value !== "string" ? null : raw.value.slice(0, 2000);
  }
  if (raw.type === "check") ev.checked = !!raw.checked;
  if (raw.type === "press") ev.key = raw.key === "Enter" || raw.key === "Tab" || raw.key === "Escape" ? raw.key : "Enter";
  if (raw.type === "observation") {
    if (typeof raw.text !== "string" || !raw.text) return null;
    ev.text = raw.text.slice(0, 500);
  }
  return ev;
}

async function broadcast(recording) {
  const tabs = await chrome.tabs.query({ url: CFG.allowedOrigins.map((o) => o + "/*") });
  await Promise.all(
    tabs.map((t) => chrome.tabs.sendMessage(t.id, { type: "gf:recording-state", recording }).catch(() => {}))
  );
}

async function upload(payload) {
  const res = await fetch(CFG.backendUrl + "/api/recordings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = typeof body.detail === "string" ? body.detail : "la grabación fue rechazada";
    throw new Error("HTTP " + res.status + ": " + detail);
  }
  return body;
}

async function uploadPending() {
  const payload = await withState((s) => s.pending);
  if (!payload) return;
  try {
    const body = await upload(payload);
    await withState((s) => {
      s.pending = null;
      s.lastUpload = { status: "uploaded", recordingId: body.id, eventCount: body.event_count, at: new Date().toISOString() };
    });
  } catch (e) {
    await withState((s) => {
      s.lastUpload = { status: "failed", error: String((e && e.message) || e).slice(0, 300), at: new Date().toISOString() };
    });
  }
}

async function handle(msg, sender) {
  switch (msg && msg.type) {
    case "gf:status":
      return withState((s) => publicState(s));

    case "gf:is-recording":
      return withState((s) => ({ recording: s.recording }));

    case "gf:start": {
      const state = await withState((s) => {
        if (!s.recording) {
          Object.assign(s, initialState(), {
            recording: true,
            name: String(msg.name || "").trim().slice(0, 120) || "Grabación " + new Date().toLocaleString(),
            startedAt: new Date().toISOString()
          });
        }
        return publicState(s);
      });
      await broadcast(true);
      return state;
    }

    case "gf:stop": {
      const hasEvents = await withState((s) => {
        if (!s.recording) return false;
        s.recording = false;
        if (s.events.length === 0) {
          s.lastUpload = { status: "empty", at: new Date().toISOString() };
          return false;
        }
        s.pending = {
          name: s.name,
          started_at: s.startedAt,
          ended_at: new Date().toISOString(),
          source: "chrome-extension",
          extension_version: chrome.runtime.getManifest().version,
          events: s.events
        };
        s.events = [];
        return true;
      });
      await broadcast(false);
      if (hasEvents) await uploadPending();
      return withState((s) => publicState(s));
    }

    case "gf:retry-upload":
      await uploadPending();
      return withState((s) => publicState(s));

    case "gf:discard":
      return withState((s) => {
        s.pending = null;
        s.lastUpload = null;
        return publicState(s);
      });

    case "gf:event": {
      // Only top-level frames of authorized tabs may contribute events.
      if (!sender.tab || sender.frameId !== 0 || !isAllowedUrl(sender.url)) return { ok: false };
      return withState((s) => {
        if (!s.recording) return { ok: false };
        if (s.events.length >= CFG.maxEvents) {
          s.dropped += 1;
          return { ok: false };
        }
        const ev = cleanEvent(msg.event, s.events.length);
        if (!ev) return { ok: false };
        s.events.push(ev);
        return { ok: true };
      });
    }

    default:
      return { ok: false, error: "unknown message" };
  }
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  // Messages from web pages cannot reach onMessage (no externally_connectable), only our
  // own popup and content scripts.
  if (sender.id !== chrome.runtime.id) return false;
  handle(msg, sender).then(sendResponse, (e) => sendResponse({ ok: false, error: String((e && e.message) || e) }));
  return true;
});
