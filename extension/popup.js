"use strict";

const $ = (id) => document.getElementById(id);

function send(type, extra) {
  return chrome.runtime.sendMessage(Object.assign({ type }, extra || {}));
}

function render(s) {
  if (!s) return;
  const state = $("state");
  if (s.recording) {
    state.dataset.state = "recording";
    state.textContent = "● Grabando «" + s.name + "» — " + s.eventCount + " eventos";
  } else {
    state.dataset.state = "idle";
    state.textContent = "Inactivo";
  }
  $("start").disabled = s.recording;
  $("stop").disabled = !s.recording;
  $("name").disabled = s.recording;

  const up = $("upload");
  const u = s.lastUpload;
  up.dataset.status = u ? u.status : "";
  up.dataset.recordingId = u && u.recordingId ? u.recordingId : "";
  if (!u) up.textContent = "";
  else if (u.status === "uploaded") up.textContent = "Grabación enviada: " + u.recordingId + " (" + u.eventCount + " eventos)";
  else if (u.status === "failed") up.textContent = "Error al enviar: " + u.error;
  else if (u.status === "empty") up.textContent = "La grabación no tenía eventos.";
  $("retry").hidden = !s.hasPending;
  $("discard").hidden = !s.hasPending;

  $("backend").textContent = s.backendUrl;
  const list = $("origins");
  list.replaceChildren(...s.allowedOrigins.map((o) => {
    const li = document.createElement("li");
    li.textContent = o;
    return li;
  }));
}

async function refresh() {
  render(await send("gf:status"));
}

$("start").addEventListener("click", async () => render(await send("gf:start", { name: $("name").value })));
$("stop").addEventListener("click", async () => {
  $("stop").disabled = true;
  render(await send("gf:stop"));
});
$("retry").addEventListener("click", async () => render(await send("gf:retry-upload")));
$("discard").addEventListener("click", async () => render(await send("gf:discard")));

refresh();
setInterval(refresh, 1000);
