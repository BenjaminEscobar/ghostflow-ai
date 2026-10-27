// Andina Servicios — fictitious demo portal for GhostFlow. All data is synthetic and stays
// in the browser (sessionStorage / localStorage). There is no server-side logic.
(function () {
  "use strict";

  var SESSION_KEY = "andina-demo-session";
  var REQUESTS_KEY = "andina-demo-requests";
  var DEMO_USER = "demo";
  var DEMO_PASSWORD = "ghostflow-demo"; // synthetic, shown on the login page

  var CATEGORY_LABELS = { soporte: "Soporte técnico", facturacion: "Facturación", logistica: "Logística" };

  function loadRequests() {
    try {
      return JSON.parse(localStorage.getItem(REQUESTS_KEY) || "[]");
    } catch (e) {
      return [];
    }
  }

  function saveRequests(list) {
    localStorage.setItem(REQUESTS_KEY, JSON.stringify(list));
  }

  function requireSession() {
    if (sessionStorage.getItem(SESSION_KEY) !== "1") {
      location.replace("index.html");
      return false;
    }
    return true;
  }

  function field(form, name) {
    return form.elements.namedItem(name);
  }

  function initLogin() {
    var form = document.querySelector("form");
    var error = document.querySelector("[role='alert']");
    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var user = field(form, "username").value.trim();
      var pass = field(form, "password").value;
      if (user === DEMO_USER && pass === DEMO_PASSWORD) {
        sessionStorage.setItem(SESSION_KEY, "1");
        location.href = "dashboard.html";
      } else {
        error.textContent = "Usuario o contraseña incorrectos.";
      }
    });
  }

  function initNewRequest() {
    if (!requireSession()) return;
    var form = document.querySelector("form");
    var error = document.querySelector("[role='alert']");
    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var data = {
        customer: field(form, "customer_name").value.trim(),
        email: field(form, "customer_email").value.trim(),
        category: field(form, "category").value,
        priority: (form.querySelector("input[name='priority']:checked") || {}).value || "baja",
        description: field(form, "description").value.trim(),
        notify: field(form, "notify").checked
      };
      if (!data.customer || !data.email || !data.category || !data.description) {
        error.textContent = "Completa todos los campos obligatorios.";
        return;
      }
      if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(data.email)) {
        error.textContent = "El correo no es válido.";
        return;
      }
      var list = loadRequests();
      data.ref = "REQ-" + (1001 + list.length);
      list.push(data);
      saveRequests(list);
      location.href = "confirmation.html?ref=" + encodeURIComponent(data.ref);
    });
  }

  function addRow(dl, term, value) {
    var dt = document.createElement("dt");
    dt.textContent = term;
    var dd = document.createElement("dd");
    dd.textContent = value;
    dl.appendChild(dt);
    dl.appendChild(dd);
  }

  function initConfirmation() {
    if (!requireSession()) return;
    var ref = new URLSearchParams(location.search).get("ref");
    var request = loadRequests().filter(function (r) { return r.ref === ref; })[0];
    var status = document.querySelector("[role='status']");
    var summary = document.querySelector("dl");
    if (!request) {
      status.textContent = "No se encontró la solicitud.";
      return;
    }
    // Rendered after a short delay, like a real app waiting for its API.
    setTimeout(function () {
      status.textContent = "Solicitud registrada correctamente";
      addRow(summary, "Referencia", request.ref);
      addRow(summary, "Cliente", request.customer);
      addRow(summary, "Correo", request.email);
      addRow(summary, "Categoría", CATEGORY_LABELS[request.category] || request.category);
      addRow(summary, "Prioridad", request.priority);
      addRow(summary, "Notificar", request.notify ? "Sí" : "No");
    }, 300);
  }

  function initRequests() {
    if (!requireSession()) return;
    var body = document.querySelector("tbody");
    loadRequests().forEach(function (r) {
      var tr = document.createElement("tr");
      [r.ref, r.customer, CATEGORY_LABELS[r.category] || r.category, r.priority].forEach(function (v) {
        var td = document.createElement("td");
        td.textContent = v;
        tr.appendChild(td);
      });
      body.appendChild(tr);
    });
  }

  var pages = {
    login: initLogin,
    dashboard: requireSession,
    "new-request": initNewRequest,
    confirmation: initConfirmation,
    requests: initRequests
  };
  var init = pages[document.body.getAttribute("data-page")];
  if (init) init();
})();
