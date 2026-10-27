// GhostFlow Recorder content script.
// Captures semantic user actions (clicks, field values, selections, checks, Enter key,
// page loads and status messages) on authorized origins while a recording is active.
// Values of sensitive fields are never sent: only the fact that the field was filled.
(function () {
  "use strict";

  if (globalThis.__ghostflowRecorderLoaded) return;
  globalThis.__ghostflowRecorderLoaded = true;

  const { isAllowedUrl, isSensitiveDescriptor, looksLikeSecret } = globalThis.GhostFlowShared;
  if (!isAllowedUrl(location.href)) return;

  const TEXT_INPUT_TYPES = new Set([
    "", "text", "email", "tel", "url", "search", "number", "password", "date", "time", "datetime-local", "month", "week"
  ]);
  const CLICKABLE = [
    "a[href]", "button", "input[type=button]", "input[type=submit]", "input[type=reset]", "input[type=image]",
    "[role=button]", "[role=link]", "[role=tab]", "[role=menuitem]", "[role=option]", "summary"
  ].join(",");
  const STATUS_REGIONS = "[role=status],[role=alert],[aria-live]:not([aria-live=off])";
  const TEXT_ROLES = new Set(["button", "link", "tab", "menuitem", "option"]);

  let recording = false;
  const lastValues = new WeakMap();
  const lastObserved = new WeakMap();

  const norm = (s) => (s || "").replace(/\s+/g, " ").trim();
  const clip = (s, n) => (s.length > n ? s.slice(0, n) : s);

  // -- element description ------------------------------------------------------------

  function implicitRole(el) {
    const tag = el.tagName.toLowerCase();
    if (tag === "a" && el.hasAttribute("href")) return "link";
    if (tag === "button") return "button";
    if (tag === "select") return el.multiple || el.size > 1 ? "listbox" : "combobox";
    if (tag === "textarea") return "textbox";
    if (tag !== "input") return null;
    const type = (el.getAttribute("type") || "text").toLowerCase();
    if (["button", "submit", "reset", "image"].includes(type)) return "button";
    if (type === "checkbox") return "checkbox";
    if (type === "radio") return "radio";
    if (type === "number") return "spinbutton";
    if (type === "range") return "slider";
    if (type === "search") return "searchbox";
    if (["text", "email", "tel", "url"].includes(type)) return "textbox";
    return null;
  }

  function textWithoutControls(node) {
    const clone = node.cloneNode(true);
    clone.querySelectorAll("input,select,textarea,button").forEach((n) => n.remove());
    return norm(clone.textContent);
  }

  function labelledBy(el) {
    const ids = (el.getAttribute("aria-labelledby") || "").split(/\s+/).filter(Boolean);
    return norm(ids.map((id) => {
      const n = document.getElementById(id);
      return n ? n.textContent : "";
    }).join(" "));
  }

  function labelText(el) {
    if (el.labels && el.labels.length) {
      return norm(Array.from(el.labels).map(textWithoutControls).join(" "));
    }
    return "";
  }

  function accessibleName(el, role, label) {
    const aria = norm(el.getAttribute("aria-label"));
    if (aria) return aria;
    const byId = labelledBy(el);
    if (byId) return byId;
    if (label) return label;
    if (TEXT_ROLES.has(role)) {
      const text = norm(el.innerText || el.textContent);
      if (text) return text;
      if (el.tagName === "INPUT") return norm(el.value || el.getAttribute("alt"));
    }
    return norm(el.getAttribute("title")) || norm(el.getAttribute("placeholder"));
  }

  function cssPath(el) {
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && parts.length < 8) {
      const tag = node.tagName.toLowerCase();
      if (node.id && /^[A-Za-z_][\w-]*$/.test(node.id)) {
        parts.unshift("#" + node.id);
        break;
      }
      if (tag === "body" || tag === "html") {
        parts.unshift(tag);
        break;
      }
      let index = 1;
      let sib = node;
      while ((sib = sib.previousElementSibling)) if (sib.tagName === node.tagName) index++;
      parts.unshift(tag + ":nth-of-type(" + index + ")");
      node = node.parentElement;
    }
    return parts.join(" > ");
  }

  function describe(el) {
    const tag = el.tagName.toLowerCase();
    const role = el.getAttribute("role") || implicitRole(el);
    const label = labelText(el);
    const type = tag === "input" ? (el.getAttribute("type") || "text").toLowerCase() : null;
    const d = {
      tag,
      role,
      accessible_name: accessibleName(el, role, label),
      label,
      test_id: el.getAttribute("data-testid"),
      element_id: el.id,
      name_attr: el.getAttribute("name"),
      placeholder: el.getAttribute("placeholder"),
      text: TEXT_ROLES.has(role) ? norm(el.innerText || el.textContent) : null,
      input_type: type,
      autocomplete: el.getAttribute("autocomplete"),
      value_attr: type === "radio" || type === "checkbox" ? el.getAttribute("value") : null,
      css: cssPath(el)
    };
    const out = {};
    for (const [k, v] of Object.entries(d)) {
      if (v !== null && v !== undefined && v !== "") out[k] = clip(String(v), k === "css" ? 500 : 200);
    }
    return out;
  }

  // -- emission -----------------------------------------------------------------------

  function send(event) {
    if (!recording) return;
    event.ts = Date.now();
    event.url = location.href;
    if (document.title) event.title = clip(document.title, 200);
    try {
      chrome.runtime.sendMessage({ type: "gf:event", event }).catch(() => {});
    } catch (e) {
      // Extension was reloaded; this page's script is orphaned.
      recording = false;
    }
  }

  function emitValue(el, type) {
    const target = describe(el);
    const value = el.value || "";
    const sensitive = isSensitiveDescriptor(target) || looksLikeSecret(value);
    // Dedupe key never holds a sensitive value.
    const key = sensitive ? "sensitive:" + value.length : value;
    if (lastValues.get(el) === key) return;
    lastValues.set(el, key);
    send({ type, target, value: sensitive ? null : clip(value, 2000), sensitive });
  }

  function scanStatusRegions() {
    if (!recording) return;
    document.querySelectorAll(STATUS_REGIONS).forEach((region) => {
      const text = clip(norm(region.innerText || region.textContent), 500);
      if (!text || lastObserved.get(region) === text) return;
      lastObserved.set(region, text);
      if (!looksLikeSecret(text)) send({ type: "observation", text });
    });
  }

  // -- listeners (capture phase, so page handlers cannot hide events) ----------------

  document.addEventListener("click", (ev) => {
    if (!recording || !ev.isTrusted) return;
    const path = ev.composedPath ? ev.composedPath() : [ev.target];
    const el = path.find((n) => n instanceof Element && n.matches(CLICKABLE));
    if (!el) return;
    send({ type: "click", target: describe(el) });
  }, true);

  document.addEventListener("change", (ev) => {
    if (!recording) return;
    const el = ev.target;
    if (!(el instanceof Element)) return;
    const tag = el.tagName.toLowerCase();
    // Select changes are accepted even if synthetic (some UIs and test drivers dispatch
    // them from script); everything else must come from the user.
    if (tag === "select") return emitValue(el, "select");
    if (!ev.isTrusted) return;
    if (tag === "input" && (el.type === "checkbox" || el.type === "radio")) {
      send({ type: "check", target: describe(el), checked: el.checked });
    } else if (tag === "textarea" || (tag === "input" && TEXT_INPUT_TYPES.has(el.type))) {
      emitValue(el, "input");
    }
  }, true);

  document.addEventListener("keydown", (ev) => {
    if (!recording || !ev.isTrusted || ev.key !== "Enter" || ev.isComposing) return;
    const el = ev.target;
    if (!(el instanceof HTMLInputElement) || !TEXT_INPUT_TYPES.has(el.type)) return;
    // The value's change event fires after keydown; emit it first to keep the order.
    emitValue(el, "input");
    send({ type: "press", target: describe(el), key: "Enter" });
  }, true);

  let scanTimer = null;
  new MutationObserver(() => {
    clearTimeout(scanTimer);
    scanTimer = setTimeout(scanStatusRegions, 250);
  }).observe(document.documentElement, { subtree: true, childList: true, characterData: true });

  // -- recording state ----------------------------------------------------------------

  function setRecording(on) {
    const started = !recording && on;
    recording = !!on;
    if (started) {
      send({ type: "navigation" });
      scanStatusRegions();
    }
  }

  chrome.runtime.onMessage.addListener((msg) => {
    if (msg && msg.type === "gf:recording-state") setRecording(msg.recording);
  });

  chrome.runtime.sendMessage({ type: "gf:is-recording" })
    .then((res) => setRecording(res && res.recording))
    .catch(() => {});
})();
