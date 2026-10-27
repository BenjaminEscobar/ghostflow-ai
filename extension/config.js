// GhostFlow Recorder configuration.
// Keep `allowedOrigins` in sync with manifest.json (content_scripts.matches and
// host_permissions) and with GHOSTFLOW_ALLOWED_ORIGINS on the backend.
globalThis.GHOSTFLOW_CONFIG = Object.freeze({
  backendUrl: "http://localhost:8010",
  allowedOrigins: Object.freeze(["http://localhost:8080", "http://127.0.0.1:8080"]),
  maxEvents: 2000
});
