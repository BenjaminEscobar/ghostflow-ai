"""The reference demo scenario: log in to the demo portal and create a customer request.

`demo_recording` returns the events the Chrome extension emits for this scenario (same
descriptors content.js computes), so backend tests do not need a browser.
"""

DEMO_USER = "demo"
DEMO_PASSWORD = "ghostflow-demo"  # synthetic credential of the fictitious demo portal

CUSTOMER = "Ana Torres"
EMAIL = "ana.torres@ejemplo.test"
DESCRIPTION = "Factura duplicada del mes de octubre"
SUCCESS_TEXT = "Solicitud registrada correctamente"


def demo_recording(base_url: str, *, leak_password: bool = False) -> dict:
    """`leak_password=True` simulates a buggy/tampered client that sends the password."""
    base_url = base_url.rstrip("/")
    events: list[dict] = []
    clock = [1_790_000_000_000]

    def ev(kind: str, path: str, after_ms: int, **fields) -> None:
        clock[0] += after_ms
        events.append({"seq": len(events), "ts": clock[0], "type": kind, "url": base_url + path, **fields})

    login = "/index.html"
    ev("navigation", login, 0, title="Ingreso · Portal Andina Servicios (demo)")
    ev("input", login, 2000, value=DEMO_USER, target={
        "tag": "input", "role": "textbox", "accessible_name": "Usuario", "label": "Usuario",
        "test_id": "login-username", "element_id": "username", "name_attr": "username",
        "input_type": "text", "autocomplete": "username", "css": "#username",
    })
    ev("input", login, 1500, value=DEMO_PASSWORD if leak_password else None, sensitive=not leak_password, target={
        "tag": "input", "label": "Contraseña", "accessible_name": "Contraseña",
        "test_id": "login-password", "element_id": "password", "name_attr": "password",
        "input_type": "password", "autocomplete": "current-password", "css": "#password",
    })
    ev("click", login, 10, target={
        "tag": "button", "role": "button", "accessible_name": "Ingresar", "test_id": "login-submit",
        "text": "Ingresar", "css": "#login-form > button:nth-of-type(1)",
    })
    ev("navigation", "/dashboard.html", 400, title="Panel · Portal Andina Servicios (demo)")
    ev("click", "/dashboard.html", 1500, target={
        "tag": "a", "role": "link", "accessible_name": "Nueva solicitud", "test_id": "nav-new-request",
        "text": "Nueva solicitud", "css": "body > main:nth-of-type(1) > nav:nth-of-type(1) > a:nth-of-type(1)",
    })
    form = "/new-request.html"
    ev("navigation", form, 300, title="Nueva solicitud · Portal Andina Servicios (demo)")
    ev("input", form, 3000, value=CUSTOMER, target={
        "tag": "input", "role": "textbox", "accessible_name": "Nombre del cliente", "label": "Nombre del cliente",
        "test_id": "customer-name", "element_id": "customer-name", "name_attr": "customer_name",
        "input_type": "text", "css": "#customer-name",
    })
    ev("input", form, 3000, value=EMAIL, target={
        "tag": "input", "role": "textbox", "accessible_name": "Correo del cliente", "label": "Correo del cliente",
        "element_id": "customer-email", "name_attr": "customer_email", "placeholder": "cliente@ejemplo.test",
        "input_type": "email", "css": "#customer-email",
    })
    ev("select", form, 1500, value="facturacion", target={
        "tag": "select", "role": "combobox", "accessible_name": "Categoría", "label": "Categoría",
        "test_id": "category", "element_id": "category", "name_attr": "category", "css": "#category",
    })
    ev("check", form, 1000, checked=True, target={
        "tag": "input", "role": "radio", "accessible_name": "Alta", "label": "Alta", "name_attr": "priority",
        "input_type": "radio", "value_attr": "alta",
        "css": "#request-form > fieldset:nth-of-type(1) > label:nth-of-type(3) > input:nth-of-type(1)",
    })
    ev("input", form, 4000, value=DESCRIPTION, target={
        "tag": "textarea", "role": "textbox", "accessible_name": "Descripción", "label": "Descripción",
        "element_id": "description", "name_attr": "description", "css": "#description",
    })
    ev("check", form, 1000, checked=True, target={
        "tag": "input", "role": "checkbox", "accessible_name": "Notificar al cliente por correo",
        "label": "Notificar al cliente por correo", "element_id": "notify", "name_attr": "notify",
        "input_type": "checkbox", "css": "#notify",
    })
    ev("click", form, 1500, target={
        "tag": "button", "role": "button", "accessible_name": "Crear solicitud", "test_id": "submit-request",
        "element_id": "submit-request", "text": "Crear solicitud", "css": "#submit-request",
    })
    done = "/confirmation.html?ref=REQ-1001"
    ev("navigation", done, 300, title="Confirmación · Portal Andina Servicios (demo)")
    ev("observation", done, 400, text=SUCCESS_TEXT)

    return {
        "name": "Registrar solicitud de cliente",
        "source": "fixture",
        "extension_version": "0.1.0",
        "events": events,
    }
