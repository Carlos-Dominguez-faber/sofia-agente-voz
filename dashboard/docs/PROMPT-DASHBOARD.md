# PROMPT-DASHBOARD — Genera tu dashboard con tu propio agente

> Este documento es el que reemplaza la clase. El dashboard fue la única pieza
> del sistema que **no** se construyó en cámara. Aquí tienes el prompt
> copy-paste para que tu agente (Claude Code) construya las dos mitades —los
> endpoints de lectura y el panel— apuntando a **tu** backend en Modal.
>
> Léelo completo antes de pegarlo. La sección de **prerrequisitos** trae un paso
> que, si te lo saltas, hace que nada compile.

---

## Antes de empezar: qué necesitas tener

1. **El agente de voz ya desplegado en Modal.** Este dashboard lee de tu backend;
   no funciona sin él. Necesitas tu URL pública de Modal (la que imprime
   `modal deploy`).
2. **Acceso a tu Location de GoHighLevel** con contactos, calendario y pipeline —lo
   mismo que ya usa Sofía—.
3. **Tu cuenta de Retell** con el agente inbound creado y su `llm_id`.
4. **Node 20+** y **Python 3.11+** en tu máquina.

---

## Prerrequisito que NO puedes saltarte: el refactor de `main.py`

El backend de Sofía tiene dos funciones dentro de `app/main.py` que el análisis
post-llamada usa para leer el payload de Retell:

- `_transcript_from(call)` — saca la transcripción del payload.
- `_phone_from_tool_calls(call)` — recupera el teléfono del paciente de los tool
  calls.

**El dashboard necesita exactamente ese mismo parseo** —es como arma el join entre
Retell y GHL—. Si dejas esas funciones dentro de `main.py`, tu agente va a
duplicarlas, y vas a tener dos parsers del mismo payload divergiendo con el tiempo.

**Antes de generar el dashboard, muévelas a un módulo compartido.** Este es el paso
que, si se te olvida, hace que el código generado no compile (importa de un módulo
que no existe todavía):

1. Crea `app/services/call_parsing.py`.
2. Mueve ahí las dos funciones, **renombrándolas sin el guion bajo inicial** (dejan
   de ser privadas de `main.py`): `transcript_from` y `phone_from_tool_calls`.
3. En `main.py`, bórralas y agrega el import:
   ```python
   from app.services.call_parsing import phone_from_tool_calls, transcript_from
   ```
4. Reemplaza los usos internos: `_transcript_from(` → `transcript_from(` y
   `_phone_from_tool_calls(` → `phone_from_tool_calls(`.
5. Corre el backend una vez (`uvicorn app.main:web_app --reload`) y confirma que
   `/health` responde antes de seguir. Si importa, el refactor quedó bien.

> El agente de Sofía se construyó en cámara **sin** `call_parsing.py`. Este paso es
> lo que lo prepara para compartir su parseo con el dashboard. No lo omitas.

---

## El prompt — pégalo en Claude Code

Copia todo lo que sigue, reemplazando lo que está `<ENTRE_PICOS>` con tus datos.

```
Tengo un agente de voz ("Sofía") ya desplegado en Modal: un FastAPI en
app/main.py con endpoints de acción (/create-lead, /check-availability,
/book-appointment, /update-lead-status) y un webhook (/retell-webhook), más una
capa de servicios en app/services/ (ghl_service.py para GoHighLevel,
retell_service.py para Retell, anthropic_service.py para el análisis
post-llamada). GoHighLevel es la única fuente de la verdad; el backend no guarda
estado propio.

Ya moví el parseo del payload de Retell a app/services/call_parsing.py con las
funciones transcript_from(call) y phone_from_tool_calls(call).

Quiero que construyas un dashboard para el cliente, en DOS mitades. NO toques los
endpoints de acción ni ghl_service.py.

═══════════════════════════════════════════════════════════════
MITAD 1 — Endpoints de LECTURA en el mismo FastAPI
═══════════════════════════════════════════════════════════════

Crea app/dashboard_api.py con un APIRouter bajo el prefijo /dashboard, montado en
main.py con include_router. Toda la lógica va en app/services/, no en los
handlers. Los endpoints:

  GET  /dashboard/metrics            → llamadas totales, citas agendadas por
                                        Sofía, tasa de éxito, duración promedio.
                                        Acepta ?days=N.
  GET  /dashboard/calls              → lista de llamadas: paciente, fecha,
                                        duración, ¿agendó?, resumen. Paginada.
  GET  /dashboard/calls/{call_id}    → detalle: transcripción, tools disparadas,
                                        scores del análisis.
  GET  /dashboard/funnel             → conteo por etapa del pipeline.
  GET  /dashboard/leads/temperature  → conteo hot/warm/cold por tag.
  GET  /dashboard/calls/{id}/recording → streamea el audio de la llamada (ver
                                        AUDIO abajo). NO devuelvas la URL cruda.
  GET  /dashboard/agent/prompt       → el prompt vigente (ver PROTECCIÓN abajo).
  PUT  /dashboard/agent/prompt       → publica el prompt editado a Retell.
  POST /dashboard/agent/prompt/undo  → restaura la versión anterior.
  GET  /dashboard/agent-config       → voz, velocidad, expresividad, comportamiento
                                        actuales (leídos de la versión PUBLICADA).
  POST /dashboard/agent-config       → cambia voz/comportamiento y PUBLICA a Retell
                                        (ver ESCRITURA abajo). Ambos agentes.
  POST /dashboard/outbound/call      → dispara una llamada a un número E.164.
  POST /dashboard/test-call          → "llámame para probar" con la config actual.
  GET  /dashboard/services/status    → estado de GHL, Retell, Twilio, Anthropic,
                                        backend.

Crea estos servicios:
  - app/services/dashboard_service.py → los joins Retell↔GHL y las métricas.
  - app/services/ghl_read_service.py  → lecturas de GHL. NO toques ghl_service.py;
    reusa sus helpers (_request, config_value). Y NUNCA expongas el campo del
    expediente clínico del doctor (contact.notas_clinicas): fíltralo en esta capa.
  - agrega funciones a retell_service.py: listar llamadas, traer una, leer/escribir
    el prompt vivo, disparar una llamada saliente, Y la escritura de config
    (publish_agent_change / apply_agent_config, ver ESCRITURA).

REGLAS DURAS de esta mitad:
  1. El join entre Retell y GHL es por el teléfono en E.164, que sale de
     phone_from_tool_calls(call). No inventes una tabla local; no guardes estado.
  2. "Citas agendadas por Sofía" cuenta book_appointment EXITOSOS en Retell
     (usa el filtro tool_calls de la API de Retell con success=true e
     include_total), NO los eventos del calendario de GHL. El calendario también
     tiene citas que la recepcionista puso a mano.
  3. call.list de Retell devuelve un objeto con .items (no una lista). Léelo bien:
     si la forma no es la esperada, LANZA error, no devuelvas lista vacía. Una
     lista vacía se muestra como "0 llamadas" y eso es una mentira si en realidad
     falló la lectura.
  4. Importa los submódulos de recursos de Retell (retell.resources.call, .llm,
     .agent) al cargar el módulo, en el nivel superior. El SDK los importa de
     forma perezosa y eso hace deadlock cuando el dashboard pide varias secciones
     en paralelo.
  5. AUTENTICACIÓN: los endpoints de lectura exponen datos de pacientes. Protégelos
     con un token compartido en header Authorization: Bearer, validado con
     secrets.compare_digest contra una variable de entorno DASHBOARD_API_TOKEN.
     /health se queda público. Agrega CORS para el origen del dashboard.
  6. Si una fuente falla, devuelve un error honesto (503 con la fuente nombrada),
     NUNCA un 200 con ceros.

PROTECCIÓN DEL PROMPT (importante): el prompt tiene una sección de reglas de
seguridad médica (nunca diagnostica, nunca confirma citas falsas). El cliente NO
debe poder borrarlas. En GET, reemplaza ese bloque por un marcador de texto y
manda el bloque aparte para mostrarlo en solo lectura. En PUT, vuelve a insertar
el bloque canónico (léelo del prompt del repo) donde está el marcador. Si el
marcador no está, RECHAZA el guardado con 422 — no lo adviertas, recházalo. Guarda
la versión anterior antes de publicar, para el undo.

ESCRITURA A RETELL (lo más delicado — el update+publish/versioning):
  Retell versiona el agente y su LLM, y hay borradores y versiones publicadas.
  El número sirve la ÚLTIMA PUBLICADA. `update` SOLO escribe a un borrador, así
  que si publicas voz/prompt con update a secas, "se guarda" pero el número sigue
  con la versión vieja. Además: publicar el agente publica su LLM; un LLM ya
  publicado NO se puede update (400 "Cannot update published LLM"); la salida es
  agent.create_version(base_version=N), que crea un borrador de agente + LLM
  acoplado editable (nada de crear agentes nuevos, que deja huérfanos).
  Haz UNA función publish_agent_change(agent_id, ...) idempotente:
    1. hallar la última versión PUBLICADA (nunca un borrador colgado)
    2. create_version(base_version=<publicada>)
    3. update del borrador: voz en el agente; temperatura/prompt en el LLM
       (ojo: escribes model_temperature, se lee api_model_temperature)
    4. publish(version=<borrador>)
  set_live_prompt y apply_agent_config pasan por ahí. apply_agent_config valida
  BOUNDS en el backend (voz de lista curada es-419, velocidad 0.85-1.15,
  comportamiento = 3 presets que mapean a temperatura ≤ 0.5) y aplica a AMBOS
  agentes (inbound + outbound); si el segundo falla, error honesto, no un guardado
  falso. NO expongas las perillas de latencia/turn-taking.
  Y las LECTURAS que dicen "esto es lo vivo" (current_agent_config, get_live_prompt)
  deben fijar la versión PUBLICADA — retrieve sin versión devuelve el borrador.

AUDIO (PII de paciente): Retell sirve las grabaciones desde una URL de CloudFront
SIN auth — cualquiera con la URL la reproduce. NO devuelvas recording_url al
navegador. En call_detail devuelve solo has_recording (bool). Haz
GET /dashboard/calls/{id}/recording que fetchea la URL server-side y STREAMEA los
bytes (tras el token). El navegador la pide a un proxy de Next tras el gate de
sesión. La URL cruda nunca sale del backend.

═══════════════════════════════════════════════════════════════
MITAD 2 — El panel en Next.js (App Router, TypeScript, Tailwind)
═══════════════════════════════════════════════════════════════

Un panel con estas secciones: (1) métricas, (2) temperatura, (3) funnel,
(4) llamadas recientes con detalle expandible + reproductor de audio,
(5) "Configuración de Sofía" (voz curada en dropdown, velocidad en slider acotado,
toggle de expresividad, comportamiento en 3 presets con nombre — nunca el número —,
botón guardar que publica, y botón "Llámame para probar"), (6) editor del prompt,
(7) llamada manual, (8) estado de servicios.

REGLAS DURAS de esta mitad:
  1. NINGUNA credencial en el navegador. El token del backend vive SOLO del lado
     del servidor. El panel proxea cada petición: el navegador llama a /api/* del
     propio Next, y un route handler server-side le agrega el token. Nada de
     NEXT_PUBLIC_ con llaves.
  2. El proxy NO es comodín. Cada operación permitida tiene su handler explícito
     (incluida la config y el test-call, que ESCRIBEN). Un proxy /api/[...path]
     con el token adjunto dejaría al navegador alcanzar los endpoints de ACCIÓN
     (book_appointment, etc.) — un panel que puede escribir citas no es lo que
     quieres. El audio va por su propio proxy que STREAMEA los bytes desde el
     backend (un <audio> no puede mandar el header Authorization, por eso el token
     vive en el route handler y no en la página).
  3. Segunda capa de auth: una contraseña para abrir el panel, que se cambia por
     una cookie httpOnly firmada, verificada en middleware con default-deny. Un
     cliente, una contraseña; sin tabla de usuarios.
  4. NUNCA muestres un cero cuando una fuente falló. Haz un componente que rinda
     "cargando", "dato no disponible con el motivo", o el dato — nunca un 0 de
     relleno. Una web call no tiene número de origen: muéstrala como "sin
     identificar", no como renglón roto.
  5. Los controles de config están ACOTADOS en la UI (dropdown de voces, slider
     con tope, presets con nombre) — pero el backend revalida SIEMPRE; la UI es
     conveniencia, no la barrera de seguridad.
  6. El branding (nombre, colores, logo) va en UN solo archivo de config.

Backend en Modal: <TU_URL_DE_MODAL>
Origen del dashboard: <TU_URL_DEL_PANEL, o http://localhost:3000 en local>

Construye la mitad 1 primero. No empieces el panel hasta que los endpoints
devuelvan datos reales contra mi Location de GHL y mi cuenta de Retell.
```

---

## Después de generar: cómo verificar

No lo des por bueno hasta que estos cuatro pasen, **en este orden**:

1. `GET <TU_URL>/health` → 200.
2. `GET <TU_URL>/dashboard/metrics` con el header `Authorization: Bearer <token>`
   → 200 con números reales (no ceros).
3. El mismo endpoint **sin** el header → 401. Si responde 200 sin token, la
   autenticación no quedó y tus datos de pacientes están abiertos.
4. `GET <TU_URL>/dashboard/calls/{id}/recording` **sin** token → 401; **con** token
   → streamea audio. Si la URL cruda de la grabación aparece en la respuesta de
   `/dashboard/calls/{id}`, la fuga de PII sigue abierta — arréglalo.
5. **Cambia la voz desde el panel y llama** (o usa "Llámame para probar"): la voz
   nueva se tiene que oír EN VIVO. Si "se guardó" pero suena igual, el update quedó
   en un borrador y te falta el publish — vuelve a la regla de ESCRITURA.
6. Abre el panel, entra con la contraseña, y confirma que las secciones traen datos
   —no "Dato no disponible" en todas—. Si todo está caído, casi siempre es que
   olvidaste desplegar el backend con los endpoints nuevos, o el token del panel no
   coincide con el del backend.

Y una prueba que vale por diez: **apaga tu backend y recarga el panel.** Cada
sección debe decir "Dato no disponible", no mostrar ceros. Si ves un `0`, el panel
puede mentirle a su dueño —arréglalo antes de entregarlo—.

---

## El comando de deploy (con el sufijo obligatorio)

Cuando redespliegues el backend con los endpoints nuevos:

```bash
modal deploy app/main.py::modal_app
```

El `::modal_app` no es opcional: sin él, Modal busca una variable llamada `app` y
la nuestra se llama `modal_app`. Falla antes de construir. Y recuerda que **los
archivos de datos no viajan a la imagen** salvo que los agregues explícitamente con
`.add_local_dir(...)`.
