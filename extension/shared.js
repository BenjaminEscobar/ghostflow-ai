// Rules shared by the content script and the service worker. The backend applies the same
// rules again on ingestion (backend/ghostflow/security/redaction.py).
(function () {
  "use strict";

  const CFG = globalThis.GHOSTFLOW_CONFIG;

  const SENSITIVE_INPUT_TYPES = new Set(["password", "hidden"]);
  const SENSITIVE_AUTOCOMPLETE = new Set([
    "current-password", "new-password", "one-time-code",
    "cc-number", "cc-csc", "cc-exp", "cc-exp-month", "cc-exp-year"
  ]);
  const SENSITIVE_NAME = new RegExp(
    "pass(word|wd|phrase)?|contrase|pwd|secret|token|api[-_ ]?key|\\botp\\b|\\bpin\\b|cvv|cvc|" +
    "card[-_ ]?(number|num)|tarjeta|\\bssn\\b|\\biban\\b|\\bauth\\b|authori[sz]ation|authentication|" +
    "session[-_ ]?id|cookie|credential",
    "i"
  );
  const JWT = /^eyJ[\w-]+\.[\w-]+\.[\w-]*$/;
  const BEARER = /^(bearer|basic)\s+\S+/i;
  const LONG_OPAQUE = /^[A-Za-z0-9+/=_-]{32,}$/;

  function isAllowedUrl(url) {
    try {
      const u = new URL(url);
      if (u.protocol !== "http:" && u.protocol !== "https:") return false;
      if (u.username || u.password) return false;
      return CFG.allowedOrigins.includes(u.origin);
    } catch (e) {
      return false;
    }
  }

  function isSensitiveDescriptor(d) {
    if (!d) return false;
    if (d.input_type && SENSITIVE_INPUT_TYPES.has(d.input_type.toLowerCase())) return true;
    if (d.autocomplete && d.autocomplete.toLowerCase().split(/\s+/).some((t) => SENSITIVE_AUTOCOMPLETE.has(t))) {
      return true;
    }
    return [d.label, d.accessible_name, d.element_id, d.name_attr, d.placeholder, d.test_id]
      .some((n) => n && SENSITIVE_NAME.test(n));
  }

  function looksLikeSecret(value) {
    if (!value) return false;
    const v = String(value).trim();
    return JWT.test(v) || BEARER.test(v) || (LONG_OPAQUE.test(v) && !/^\d+$/.test(v));
  }

  globalThis.GhostFlowShared = Object.freeze({ isAllowedUrl, isSensitiveDescriptor, looksLikeSecret });
})();
