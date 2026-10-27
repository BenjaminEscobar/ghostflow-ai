# Variantes del portal demo

Cada carpeta contiene solo los archivos que cambian respecto de `demo-portal/`. Se sirven
superpuestas sobre el portal original (`make portal-variant`), en el mismo origen
`http://localhost:8080`, para reproducir un workflow ya grabado contra una interfaz modificada.

- `crear-requerimiento/` (GF-003): en el panel, el enlace **Nueva solicitud** pasa a
  **Crear requerimiento**, con otro test id, otro id, otra estructura HTML (barra de herramientas
  con lista en lugar de `<nav>`) y textos vecinos distintos. El destino (`new-request.html`) no
  cambia. Todos los localizadores grabados para ese paso fallan; solo la recuperación semántica
  puede continuar.
