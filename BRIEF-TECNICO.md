---
proyecto: agente-voz-ghl
caso: Clínica Dental "Sonrisa Perfecta" · agente "Sofía"
backend: Modal + Python  ·  CRM+agenda: GoHighLevel  ·  voz: Retell  ·  número: Twilio  ·  cerebro: Claude
actualizado: 2026-07-20
---

# BRIEF TÉCNICO — `agente-voz-ghl`

> **Especificación del sistema.** Este documento describe *cómo está construido* el agente de
> voz: arquitectura, estructura de archivos, la capa de integración con GoHighLevel y sus
> endpoints exactos, la configuración de Retell y el hosting en Modal.
>
> El *qué* y el *por qué* (alcance, qué hace el agente, qué recibe el cliente) están en
> **`BRIEF.md`**, en esta misma carpeta. Los dos juntos son el contexto que le entregas a
> Claude Code al inicializar el proyecto.

## ⚡ Cómo usar este documento (léelo primero)

Hay **dos formas** de terminar con el sistema funcionando, y las dos son válidas. Elige la tuya
antes de seguir leyendo, porque cambia qué significa todo lo demás:

### Ruta A — construirlo tú

Este documento es la **especificación completa**. Se lo das a Claude Code junto con `BRIEF.md`,
y Claude construye el sistema a partir de aquí: crea `app/`, `ghl_service.py`, los prompts,
`sofia.config.yaml`, todo. Los archivos de la sección 3 **no los tienes todavía — son el
objetivo**, y van apareciendo conforme construyes.

Es el camino largo, y es el que te enseña de verdad cómo funciona por dentro.

### Ruta B — instalarlo ya hecho

Clonas el repo **`agente-voz-ghl`**, que ya viene con **todo**: el backend, los cinco nichos en
`prompts/`, `sofia.config.yaml` precargado con la clínica, el `.env.example` y las skills.
Corres `/setup`, contestas la entrevista, y en minutos tienes tu agente sonando.

Aquí este documento es **la documentación de lo que recibiste**: para entender cómo está armado
y dónde meter mano cuando quieras cambiar algo.

> **Si vas por la Ruta B, no tienes que crear un solo archivo.** Todo lo que se describe abajo
> ya existe en el repo. Lee este documento como un mapa, no como una lista de tareas.

---

## 0. Qué es este sistema (one-liner)

Un agente de voz ("Sofía") que **contesta el teléfono de la clínica 24/7**, califica al
paciente, **agenda la cita de valoración en el calendario de GHL**, **crea el contacto y la
oportunidad en GHL**, hace un **resumen post-llamada con Claude**, y además **devuelve
llamadas** (outbound) a los leads pendientes y a los no-shows. Todo configurable desde un solo
archivo (`sofia.config.yaml`) y el comando `/setup`.

## 1. Principio de diseño

**Un proveedor por capa, a propósito.** No hay base de datos aparte ni capa de automatización
extra: **GoHighLevel es la fuente de la verdad** (contactos, calendario y pipeline en una sola
subcuenta). El backend no guarda estado propio — lee y escribe en GHL.

Consecuencia práctica: si quieres saber qué pasó con un paciente, lo ves en GHL, no en un log.

## 2. Arquitectura / flujo de una llamada

```
Twilio (número)  ──SIP──▶  Retell (STT + LLM + TTS)  ──HTTP tools──▶  Modal (FastAPI, URL pública)
                                                                          │
                                                                          ├─▶ GHL: contacto, calendario, oportunidad
                                                                          └─▶ Claude: resumen + score post-llamada → GHL

   Worker outbound (Modal Cron, cada hora) ─▶ lee leads/no-shows pendientes en GHL ─▶ Retell create_phone_call

   Dashboard (Next.js, ligero) ─▶ LEE métricas de GHL y del backend
```

| Pieza | Trabajo |
|-------|---------|
| **Twilio** | El número. Recibe y hace llamadas. Conectado a Retell por Elastic SIP Trunk. |
| **Retell** | Los oídos y la voz: STT + TTS en tiempo real y la orquestación de la llamada. |
| **Claude** | El cerebro: decide qué responder y qué herramienta usar. Y el análisis post-llamada. |
| **Modal** | La cocina: el backend donde viven y se ejecutan las tools, 24/7, con URL pública. |
| **GHL** | El valor guardado: contacto, cita y avance del paciente en el pipeline. |

## 3. Estructura de archivos

> **Ruta A:** esto es el **objetivo** — lo vas creando conforme construyes.
> **Ruta B:** esto es **lo que encuentras** al clonar el repo. No creas nada.

```
agente-voz-ghl/
├── BRIEF.md                   # alcance del proyecto
├── BRIEF-TECNICO.md           # este documento
├── sofia.config.yaml          # datos del negocio (se versiona)
├── .env.example               # plantilla de credenciales (los secretos NO se versionan)
├── pyproject.toml
├── CLAUDE.md                  # contexto del proyecto + skills /setup /test /customize /status
├── prompts/
│   ├── dental.yaml            # ← nicho ancla (Sonrisa Perfecta)
│   ├── inmobiliaria.yaml  abogados.yaml  gimnasio.yaml  restaurante.yaml
├── app/
│   ├── main.py                # FastAPI sobre Modal: endpoints de tools + webhooks
│   ├── config.py              # carga sofia.config.yaml + template de industria
│   ├── outbound_worker.py     # cron: leads/no-shows pendientes → llamada
│   ├── services/
│   │   ├── ghl_service.py     # ★ el corazón: contactos + calendario + oportunidades + tags
│   │   ├── retell_service.py  # crear y gestionar agentes + llamadas
│   │   ├── twilio_service.py  # número / SIP trunk
│   │   └── anthropic_service.py # análisis post-llamada
│   └── webhooks/
│       ├── retell_handler.py  # eventos de Retell (call_started, call_ended, post-call)
│       └── twilio_handler.py
├── scripts/
│   ├── setup.py  test_services.py  customize.py  status.py  validate.py
└── dashboard/                 # Next.js (ligero — solo lee y presenta)
```

## 4. La capa GHL — `app/services/ghl_service.py`

Es el archivo central del sistema: todo lo que Sofía **hace** pasa por aquí.

> Base URL: `https://services.leadconnectorhq.com` · Header `Version: 2021-07-28` ·
> `Authorization: Bearer {HIGHLEVEL_PIT}` · `Content-Type: application/json`.
> Todo va scoped a la **Location** (la subcuenta de GHL).

| Función | Endpoint GHL | Notas |
|---------|--------------|-------|
| `upsert_contact(phone, first_name, last_name, email, tags, custom_fields)` | `POST /contacts/upsert` | body: `locationId`, `phone` **E.164**, `customFields:[{key,field_value}]`. Devuelve `contact.id` + `new`. **Idempotente por teléfono** — nunca duplica. |
| `get_free_slots(calendar_id, start_ms, end_ms, timezone)` | `GET /calendars/{calendarId}/free-slots` | `startDate`/`endDate` en **epoch ms**, `timezone` IANA. Respuesta: dict por fecha → `slots[]` ISO con offset. **Filtrar claves que no son fecha (`traceId`)**. Máximo 31 días por consulta. |
| `book_appointment(calendar_id, contact_id, start_iso, end_iso, title)` | `POST /calendars/events/appointments` | `calendarId`, `locationId`, `contactId`, `startTime`/`endTime` en **ISO 8601 con offset** (`...-06:00`), `appointmentStatus:"confirmed"`, `toNotify:true`. |
| `list_pipelines()` | `GET /opportunities/pipelines?locationId=` | Para resolver pipeline y stage durante `/setup`. |
| `create_opportunity(pipeline_id, stage_id, contact_id, name, status, monetary_value)` | `POST /opportunities/` | Crea la oportunidad en la etapa indicada. |
| `add_tags(contact_id, tags)` / `remove_tags(contact_id, tags)` | `POST` / `DELETE /contacts/:id/tags` | Temperatura del lead (hot/warm/cold) vía tags. El DELETE lleva body JSON. |
| `search_contact_by_phone(phone)` | `GET /contacts/?locationId=&query=<phone>` | Dedup y lookup. |
| `test_connection()` | `GET /locations/{locationId}` | Valida PIT + Location. Lo usa `/test`. |

### Reglas duras de esta capa

1. **Teléfono siempre en E.164** (con `+` y lada). Sin excepción.
2. **Appointments en ISO 8601 con offset**, no en UTC pelón — si mandas UTC, la cita aparece con la hora corrida.
3. **Si GHL falla, el agente NO miente.** El endpoint devuelve un **error honesto** y Sofía ofrece seguimiento humano ("déjame confirmártelo, en un momento te contacta una persona del equipo"). Nunca un "ya quedó agendado" falso: el paciente cuelga creyendo que tiene cita, no llega nadie a recibirlo, y pierdes al cliente y la confianza.

## 5. Endpoints de `main.py` (las tools que Retell llama en vivo)

| Endpoint | Qué hace | En GHL |
|----------|----------|--------|
| `POST /create-lead` | Crea el contacto de quien llama | `upsert_contact(...)` |
| `POST /check-availability` | Consulta horarios libres | `get_free_slots(...)` |
| `POST /book-appointment` | Agenda la cita | `upsert_contact` → `book_appointment` (+ `create_opportunity` en "Cita Agendada") |
| `POST /update-lead-status` | Mueve temperatura y etapa | `add_tags` + `create_opportunity`/stage |
| `POST /post-call-summary` | Resumen + score | `anthropic_service` → nota y custom fields en el contacto |
| `POST /retell-webhook` | Eventos de llamada (`call_ended` dispara el post-call) | — |
| `POST /trigger-outbound` + `outbound_cron` | Worker de seguimiento | Lee leads y no-shows pendientes en GHL |
| `GET /health` | Healthcheck | — |

> **Dental no necesita "buscar inventario"** (a diferencia de una inmobiliaria): los tratamientos
> son una lista fija, así que viven en el Knowledge Base del prompt y en `sofia.config.yaml`,
> no en una base de datos consultable. Por eso no hay un endpoint `search-products`.

## 6. Modal (hosting del backend)

- App Modal `agente-voz-ghl`; imagen `debian_slim` + pip: `retell-sdk`, `twilio`, `anthropic`, `requests`, `pyyaml`, `fastapi`.
- `@modal.asgi_app()` expone FastAPI → **URL pública** que Retell usa como base de sus custom tools.
- Secret de Modal (`agente-voz-credentials`) con las variables de la sección 9.
- **Worker outbound:** `@modal_app.function(schedule=modal.Cron("0 * * * *"))` → `run_outbound_cycle()`.
- Deploy: `modal token new` (autenticación por CLI, una sola vez) → `modal deploy`.

## 7. Retell (configuración del agente)

Dos agentes: **Sofía inbound** y **Sofía outbound**. Se crean por API/SDK desde Claude Code, no a mano.

| Parámetro | Valor inicial |
|-----------|---------------|
| LLM | **Claude Haiku** (rápido y costo-efectivo) |
| Temperature | **0.3–0.4** (precisión sobre creatividad) |
| Voz | **ElevenLabs** es-MX, tono cálido (`voice_id` en el config) |
| Idioma | `es-MX` |
| Speak during execution | **ON** — habla mientras consulta disponibilidad, evita el silencio incómodo |
| Latencia / turn-taking | Sensibilidad a interrupciones, detección de fin de turno, pausas |
| Tools (custom functions) | Apuntan a la URL pública de Modal (sección 5) |
| Webhook | `POST /retell-webhook` para eventos de llamada |

### El prompt en 12 componentes

Role · Context · Personality · Task · Specifics · Conversational Flow · Knowledge Base ·
Style Guardrails · Response Guidelines · **Global Timing & Pacing Rules** ·
Safety & Scope Guardrails · Objection Handling.

### Reglas de pacing (lo que hace que no suene a robot)

- Teléfonos **dígito a dígito**, confirmando al final.
- Correos **deletreados**.
- **Pausa de 3 segundos** tras consultar disponibilidad, antes de ofrecer horarios.
- Horarios dichos **una sola vez**, en formato hablado ("el martes a las cuatro de la tarde").
- **Precisión y claridad por encima de velocidad.** Cuando dude, frena.
- **Nunca diagnostica** — "el doctor te valora en la cita".
- **Prioriza urgencias**: dolor, hinchazón o sangrado.

## 8. `sofia.config.yaml` + `prompts/dental.yaml`

> **Ruta B:** estos dos archivos ya vienen en el repo, precargados con la Clínica Dental
> "Sonrisa Perfecta". No los escribes: los ajustas con `/customize` o a mano cuando cambies
> de negocio. Los cinco nichos de `prompts/` también vienen incluidos.

**`sofia.config.yaml`** — datos del negocio, se versiona:
`business.name: "Clínica Dental Sonrisa Perfecta"`, `industry: dental`,
`timezone: America/Cancun`, `agent.name: Sofía`, voz ElevenLabs es-MX, outbound activo.
El bloque `crm` lleva el mapeo a GHL: custom field keys, `calendar_id`, `pipeline_id`, `stage_id`.

**`prompts/dental.yaml`** — prompts inbound y outbound + análisis post-llamada.
`action_label: "Agendar cita de valoración"`. Tratamientos como Knowledge Base con precios
aproximados: limpieza ~$800, blanqueamiento ~$3,500, ortodoncia ~$500 mensuales,
implante ~$15,000, endodoncia ~$4,000.

> Los otros cuatro nichos (`inmobiliaria`, `abogados`, `gimnasio`, `restaurante`) tienen la
> **misma estructura** — solo cambian las preguntas de calificación, el vocabulario, el tono y
> la acción que cierra.

## 9. `.env.example` (credenciales)

```bash
# RETELL (voz)
RETELL_API_KEY=
RETELL_INBOUND_AGENT_ID=        # lo llena /setup
RETELL_OUTBOUND_AGENT_ID=       # lo llena /setup

# TWILIO (número)
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_PHONE_NUMBER=            # E.164, con + y lada

# GOHIGHLEVEL (CRM + calendario + pipeline)
HIGHLEVEL_PIT=                  # Private Integration Token (scopes: contacts, calendars, opportunities)
HIGHLEVEL_LOCATION_ID=
HIGHLEVEL_CALENDAR_ID=          # calendario "Valoración dental"
HIGHLEVEL_PIPELINE_ID=          # pipeline "Nuevos Pacientes"
HIGHLEVEL_STAGE_ID=             # etapa por defecto (Cita Agendada)

# ANTHROPIC (análisis post-llamada)
ANTHROPIC_API_KEY=

# MODAL — se autentica por CLI (modal token new). NO va aquí.
```

> `.env` va en `.gitignore`. Lo que se versiona es `.env.example`, vacío, para que cualquiera
> sepa qué credenciales hacen falta.

## 10. Skills (`CLAUDE.md`)

| Comando | Qué hace |
|---------|----------|
| `/setup` | Entrevista interactiva (pensada para no-devs) → llena `sofia.config.yaml` → pide credenciales y **valida cada API en vivo** (incluido `GET /locations/{id}` para GHL) → crea los agentes de Retell, conecta Twilio, referencia el calendario y el pipeline de GHL, y despliega a Modal. Admite `--skip-interview`. |
| `/test` | Verifica **5 servicios**: Retell · Twilio · GHL (contactos, calendario y pipeline) · Backend (health de Modal) · Anthropic. Los errores **siempre** vienen con la solución, nunca con un código crudo. |
| `/customize` | Ajusta prompt, campos y tags de GHL, horario de outbound, datos del negocio o voz — sin romper las reglas de pacing ni las guardrails. |
| `/status` | Estado en vivo de todos los servicios y la última llamada. |

## 11. Orden de construcción recomendado (Ruta A)

> Solo aplica si estás construyendo. Si clonaste el repo, sáltate esta sección: ya está todo hecho.

Si vas a levantarlo por partes, este orden evita bloqueos:

1. **La capa GHL** (`ghl_service.py`) y su `test_connection`, probada contra una Location real.
2. **Los endpoints de `main.py`** y los webhooks, con el manejo de error honesto en booking.
3. **El análisis post-llamada** → resumen y score como nota + custom fields en GHL.
4. **Precargar el negocio** (`sofia.config.yaml` + el nicho en `prompts/`).
5. **Las skills** `/setup`, `/test`, `/customize`, `/status`.
6. **Deploy a Modal** y creación de los agentes de Retell (inbound y outbound).
7. **El dashboard** — al final, es la capa de presentación.

> **Las herramientas van antes que el agente.** De nada sirve una recepcionista brillante sin
> acceso al sistema de citas: primero las manos, luego la voz.

## 12. Lo que NO es el foco

- **El dashboard.** Es la capa de presentación: solo lee y presenta. No invertir tiempo de build ahí.
- **Soportar varios CRMs en paralelo.** GHL reemplaza al CRM y al calendario, no convive con otros.
- **Optimizar costos antes de que funcione.** Primero que agende bien; después se afina.
