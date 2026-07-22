# CLAUDE.md — `agente-voz-ghl`

> Contexto del proyecto para Claude Code. El alcance vive en [BRIEF.md](BRIEF.md);
> el detalle técnico en [BRIEF-TECNICO.md](BRIEF-TECNICO.md). Este archivo es el mapa
> que se lee al inicio de cada sesión.

---

## 1. Qué construimos

Un **agente de voz** llamado **Sofía**: la recepcionista de la **Clínica Dental "Sonrisa
Perfecta"**. Contesta el teléfono 24/7, califica al paciente y **agenda una CITA DE
VALORACIÓN** en el calendario real del negocio.

En concreto, Sofía:

1. **Contesta 24/7** con voz natural en español de México.
2. **Califica:** motivo de la llamada, síntoma y urgencia (dolor, hinchazón, sangrado →
   prioriza), tratamiento de interés, datos de contacto. **Nunca diagnostica.**
3. **Agenda** la cita de valoración en el calendario de GoHighLevel.
4. **Llena el CRM:** crea el contacto, abre la oportunidad en el pipeline "Nuevos Pacientes"
   y pone tags de temperatura (hot / warm / cold).
5. **Resume la llamada** al colgar (Claude lee la transcripción) y guarda nota + score en la
   ficha del contacto.
6. **Devuelve llamadas (outbound):** un worker en Modal revisa GHL cada hora y llama a
   no-shows y leads frescos para recalificar y reagendar.

Caso ancla deliberado: el mismo negocio y personaje del curso de GoHighLevel y del agente de
WhatsApp, para que toda la serie sea coherente.

### Principio de diseño

**Un proveedor por capa, a propósito.** No hay base de datos aparte ni capa de automatización
extra. **GoHighLevel es la fuente de la verdad** — contactos, calendario y pipeline en una
sola subcuenta (Location). El backend **no guarda estado propio**: lee y escribe en GHL.

Consecuencia práctica: si quieres saber qué pasó con un paciente, lo ves en GHL, no en un log.

---

## 2. El flujo completo de una llamada

```
1. El paciente marca el número Twilio
        │
        ▼
2. Twilio enruta por Elastic SIP Trunk ──▶ Retell
        │
        ▼
3. Retell: STT (oye) · LLM (decide) · TTS (habla) — orquesta el turno de conversación
        │
        │  durante la llamada, cuando necesita hacer algo, llama tools HTTP:
        ▼
4. Modal (FastAPI, URL pública) — el backend donde viven las tools
        │
        ├─▶ POST /create-lead        → GHL: upsert del contacto
        ├─▶ POST /check-availability → GHL: free-slots del calendario
        ├─▶ POST /book-appointment   → GHL: upsert + cita + oportunidad
        └─▶ POST /update-lead-status → GHL: tags de temperatura + etapa del pipeline
        │
        ▼
5. Cuelga la llamada → Retell dispara `call_ended` a POST /retell-webhook
        │
        ▼
6. Modal llama a Claude con la transcripción → resumen + score estructurado
        │
        ▼
7. GHL: la nota y los custom fields quedan en la ficha del contacto,
   con la cita en el calendario y la oportunidad en el pipeline.
```

Y en paralelo, el ciclo outbound:

```
Modal Cron (cada hora) ─▶ lee leads pendientes y no-shows en GHL
                       ─▶ Retell `create_phone_call` (agente Sofía outbound)
                       ─▶ mismo flujo de tools de arriba
```

---

## 3. El stack y el rol exacto de cada pieza

| Pieza           | Rol exacto                                                                                                                                                                                      |
| --------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Retell AI**   | **La voz y los oídos.** STT + TTS en tiempo real y la orquestación del turno de conversación. Aquí viven los dos agentes (Sofía inbound y Sofía outbound) y sus custom tools apuntando a Modal. |
| **Twilio**      | **El número.** Recibe y hace las llamadas. Se conecta a Retell por **Elastic SIP Trunk**. Nada de lógica de negocio vive aquí.                                                                  |
| **Claude**      | **El cerebro.** Razona en llamada (qué responder, qué tool usar) y hace el **análisis post-llamada**: lee la transcripción y devuelve resumen + score estructurado que se escribe en GHL.       |
| **Modal**       | **La cocina.** El backend Python/FastAPI donde viven y se ejecutan las tools, 24/7, con URL pública. También hospeda el worker Cron de outbound.                                                |
| **GoHighLevel** | **El valor guardado.** CRM + calendario + pipeline en una sola Location: contacto, cita y avance del paciente. Fuente única de la verdad.                                                       |

> **Nota sobre el LLM en llamada.** Dentro de Retell usamos **Claude Haiku · temperature
> 0.3–0.4**. Haiku es el modelo rápido de Anthropic: en voz mandan la **latencia** y el
> **costo por minuto**, no el tamaño del modelo. Evitar modelos de razonamiento — añaden
> 1–2 s de silencio por turno y matan la conversación.
>
> Así que **Claude es el cerebro de punta a punta**: razona en llamada y hace el análisis
> post-llamada. El catálogo de modelos de Retell rota — verificar el nombre exacto del
> selector y el precio en `retellai.com/pricing` antes de fijarlo.

### Otros valores de referencia

- **Voz:** voz de plataforma de Retell, **`retell-Andrea`**, femenina mexicana, a
  `voice_speed` 0.9 · idioma **`es-419`** (español de Latinoamérica — **`es-MX` no es un
  valor válido en Retell**) · **speak-during-execution ON** (habla mientras consulta
  disponibilidad, evita el silencio incómodo).
- **GHL API:** base `https://services.leadconnectorhq.com` · header `Version: 2021-07-28`.
- **Número Twilio local MX:** ~$6.25/mes · entrante ~$0.01/min.
- **Toll-free +52 800:** ~$30/mes · entrante ~$0.216/min.

---

## 4. Configuración: `sofia.config.yaml` vs `.env`

Dos archivos, dos naturalezas distintas. **No mezclar.**

|                  | `sofia.config.yaml`                                                                                                                                                                                           | `.env`                                                                                            |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| **Qué guarda**   | Datos del negocio: nombre, industria, timezone, nombre del agente, `voice_id`, horarios, outbound activo, y el bloque `crm` con el mapeo a GHL (custom field keys, `calendar_id`, `pipeline_id`, `stage_id`). | Secretos: API keys y tokens.                                                                      |
| **Se versiona**  | **Sí.** Es parte del repo — describe el negocio, no da acceso a nada.                                                                                                                                         | **No.** Va en `.gitignore`.                                                                       |
| **Se comparte**  | Sí, precargado con "Sonrisa Perfecta".                                                                                                                                                                        | Nunca. Se versiona solo `.env.example`, **vacío**, para que se sepa qué credenciales hacen falta. |
| **Se edita con** | `/customize` o a mano.                                                                                                                                                                                        | `/setup` (entrevista) o a mano.                                                                   |

Regla dura: **si un valor identifica al negocio, va al YAML; si da acceso a un servicio, va al
`.env`.** Nunca hardcodear credenciales en el código.

> **Estado actual:** `sofia.config.yaml` ya existe y está precargado con la clínica ancla.
> Lo edita `/setup` (entrevista) o `/customize`; a mano solo si sabes qué tocas.

---

## 5. Credenciales

Las credenciales conectadas por `.env` son **cuatro**:

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
HIGHLEVEL_PIT=                  # Private Integration Token
HIGHLEVEL_LOCATION_ID=          # scopes: contacts, calendars, opportunities
HIGHLEVEL_CALENDAR_ID=          # calendario "Valoración dental"
HIGHLEVEL_PIPELINE_ID=          # pipeline "Nuevos Pacientes"
HIGHLEVEL_STAGE_ID=             # etapa por defecto (Cita Agendada)

# ANTHROPIC (cerebro + análisis post-llamada)
ANTHROPIC_API_KEY=
```

> **Modal NO va en `.env`.** Se autentica por CLI, una sola vez:
>
> ```bash
> modal token new
> ```
>
> Después de eso, `modal deploy` funciona. No busques ni pidas un `MODAL_TOKEN` —
> no existe en este proyecto. Los secretos que el backend necesita en runtime se cargan en
> el Modal Secret `agente-voz-credentials`.

---

## 6. Estructura de archivos

```
agente-voz-ghl/
├── BRIEF.md · BRIEF-TECNICO.md · CLAUDE.md · INSTALAR.md
├── .gitignore · .env.example · .env
├── sofia.config.yaml          # datos del negocio (se versiona)
├── pyproject.toml
├── prompts/
│   ├── dental.yaml            # ← nicho ancla (Sonrisa Perfecta)
│   └── inmobiliaria.yaml · abogados.yaml · gimnasio.yaml · restaurante.yaml
├── app/
│   ├── main.py                # FastAPI sobre Modal: tools + webhooks (::modal_app)
│   ├── worker.py              # app de Modal aparte: cron horario de outbound (::modal_app)
│   ├── dashboard_api.py       # endpoints /dashboard del panel · auth.py el token
│   ├── services/
│   │   ├── ghl_service.py     # ★ el corazón: contactos + calendario + oportunidades + tags
│   │   ├── ghl_read_service.py · dashboard_service.py    # lectura para el panel
│   │   ├── retell_service.py · twilio_service.py · anthropic_service.py
│   │   └── prompt_guard.py · prompt_history.py · call_parsing.py
├── scripts/                   # setup · validate · test_services · customize · status
├── tests/
└── dashboard/                 # Next.js — el panel de control (ver sección 8)
```

> **Son DOS apps de Modal, no una.** `app/main.py` y `app/worker.py` se despliegan por
> separado, ambas con el sufijo `::modal_app`. Desplegar solo la primera deja el outbound
> ausente en silencio: nada falla, las devoluciones de llamada simplemente nunca ocurren.

---

## 7. Reglas duras

### De la capa GHL (`ghl_service.py`)

1. **Teléfono siempre en E.164** (con `+` y lada). Sin excepción.
2. **Appointments en ISO 8601 con offset** (`...-06:00`), **no en UTC pelón** — si mandas UTC,
   la cita aparece con la hora corrida.
3. **`upsert_contact` es idempotente por teléfono** — nunca duplica.
4. **`get_free_slots`:** fechas en **epoch ms**, `timezone` IANA, máximo 31 días por consulta.
   Filtrar las claves de la respuesta que **no** son fecha (`traceId`).
5. **Si GHL falla, el agente NO miente.** El endpoint devuelve un **error honesto** y Sofía
   ofrece seguimiento humano ("déjame confirmártelo, en un momento te contacta una persona
   del equipo"). Nunca un "ya quedó agendado" falso: el paciente cuelga creyendo que tiene
   cita, no llega nadie a recibirlo, y pierdes al cliente y la confianza.

### De pacing (lo que hace que no suene a robot)

- Teléfonos **en bloques y fluido al confirmar** (nunca dígito por dígito ni una pausa entre cada grupo). Correos **deletreados** solo la parte del usuario.
- **Pausa de 3 segundos** tras consultar disponibilidad, antes de ofrecer horarios.
- Horarios dichos **una sola vez**, en formato hablado ("el martes a las cuatro de la tarde").
- **Precisión y claridad por encima de velocidad.** Cuando dude, frena.
- **Nunca diagnostica** — "el doctor te valora en la cita".
- **Prioriza urgencias:** dolor, hinchazón o sangrado.

El prompt sigue la **estructura de 12 componentes**: Role · Context · Personality · Task ·
Specifics · Conversational Flow · Knowledge Base · Style Guardrails · Response Guidelines ·
Global Timing & Pacing Rules · Safety & Scope Guardrails · Objection Handling.

---

## 8. El panel de control (Next.js, en `dashboard/`) — en producción

Un **panel de control** en Next.js para el cliente, desplegado en Vercel. Nació como un
dashboard de solo lectura; **ya no lo es**. Hoy además de mostrar, **cambia a Sofía en
vivo**: voz, comportamiento y prompt, publicados al número real sin entrar a Retell.

Lo que **sigue siendo cierto**: **el panel NO tiene base de datos propia.** Todo lo que
muestra lo lee de dos fuentes — **GoHighLevel** (métricas, contactos, citas, funnel) y **el
backend en Modal** (llamadas, transcripciones, grabaciones, estado de servicios).

Qué hace:

- **Lee:** métricas (llamadas totales, citas agendadas por Sofía, tasa de éxito, duración
  promedio), llamadas recientes con transcripción y resumen, **grabación reproducible** de
  cada llamada, temperatura de leads y funnel.
- **Escribe (publicado en vivo):** voz y velocidad, comportamiento, y el prompt del agente —
  todo vía `publish_agent_change`, que **publica**, no deja borradores. Un cambio guardado
  pero no publicado es el bug V07/V09: el número sigue hablando con la versión anterior.
- **Dispara** una llamada outbound manual, y lleva branding por cliente.

> **Costos, no.** Se consideraron y quedaron fuera. Retell expone su costo por llamada, pero
> Twilio y Anthropic se facturan aparte y nadie los agrega hoy: una tarjeta de "costos" que
> suma solo una de las tres fuentes miente por omisión, y el dueño de la clínica la leería
> como el costo total de operar a Sofía. Cuando exista una fuente que sume las tres, entra.

> El "sin entrar a Retell" es el argumento de negocio: es lo que sostiene el
> **mantenimiento mensual**.

**Qué implica para el backend hoy:**

- Los endpoints devuelven **JSON limpio y estable**, consumible por un frontend, no solo por
  Retell.
- La lógica de negocio vive en `app/services/`, **no** en los handlers — para que el panel
  pueda reusarla sin duplicar.
- Los endpoints del panel viven en `app/dashboard_api.py`, bajo `/dashboard` y detrás del
  token compartido `DASHBOARD_API_TOKEN` (el mismo valor en el Modal Secret y en Vercel; si
  no coinciden, todo el panel responde 401). Los endpoints que usa Retell no se tocan desde
  ahí: depende de ellos en vivo.
- No introducir estado local que el dashboard tendría que sincronizar. **GHL sigue siendo la
  fuente de la verdad.** Hay **una sola excepción deliberada**, `app/services/prompt_history.py`:
  guarda la versión previa del prompt para poder revertir en un clic si un cliente borra los
  guardrails médicos. No es dato del CRM y el razonamiento está en el BLUEPRINT.

---

## 9. Skills (existen, en `.claude/commands/`)

| Comando      | Qué hace                                                                                                                                                                                                                                              |
| ------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `/setup`     | **Preflight de Python 3.12** → entrevista interactiva (para no-devs) → llena `sofia.config.yaml`, **valida cada API en vivo**, despliega backend + worker a Modal, crea Y publica los agentes de Retell, conecta Twilio (inbound + outbound), despliega el panel a Vercel. Admite `--skip-interview`. |
| `/test`      | Verifica 5 servicios: Retell · Twilio · GHL · Backend (health de Modal) · Anthropic. Los errores **siempre** vienen con la solución, nunca con un código crudo.                                                                                       |
| `/customize` | Ajusta prompt, campos y tags de GHL, horario de outbound, datos del negocio o voz — sin romper pacing ni guardrails.                                                                                                                                  |
| `/status`    | Estado en vivo de todos los servicios y la última llamada.                                                                                                                                                                                            |

---

## 10. Orden de construcción

**Las herramientas van antes que el agente.** De nada sirve una recepcionista brillante sin
acceso al sistema de citas: primero las manos, luego la voz.

1. La capa GHL (`ghl_service.py`) + `test_connection`, probada contra una Location real.
2. Los endpoints de `main.py` y los webhooks, con el manejo de error honesto en booking.
3. El análisis post-llamada → resumen y score como nota + custom fields en GHL.
4. Precargar el negocio (`sofia.config.yaml` + `prompts/dental.yaml`).
5. Las skills `/setup`, `/test`, `/customize`, `/status`.
6. Deploy a Modal + creación de los agentes de Retell (inbound y outbound).
7. El dashboard — al final, es la capa de presentación.

---

## 11. Lo que NO es el foco

- **Agrandar el panel.** Ya está entregado y en producción. Lo que muestra y lo que edita
  está cerrado a propósito: cada control nuevo es una forma nueva de que un cliente rompa a
  Sofía. El trabajo vive en el agente y en el instalador, no ahí.
- **Soportar varios CRMs en paralelo.** GHL reemplaza al CRM y al calendario, no convive con otros.
- **Optimizar costos antes de que funcione.** Primero que agende bien; después se afina.

---

## 12. Convenciones

- Español para conversación y para el copy del agente; **inglés para código y comentarios**.
- Commits en inglés, convencionales (`feat:` / `fix:` / `chore:`).
- Variables de entorno **nunca** hardcodeadas.
- Si algo puede romper producción (una llamada real en curso, una cita ya agendada), **avisar
  antes de ejecutar**.

## Gotchas conocidos

- **Retell outbound no cuelga solo.** Hay que agregar la tool **`end_call`** al LLM y
  configurar `end_call_after_silence_ms: 10000`. Sin eso, la llamada se queda abierta
  consumiendo minutos después de que el paciente se despide.
- **`get_free_slots` devuelve claves que no son fechas** (`traceId`). Filtrarlas antes de
  iterar o el parseo truena.
- **Citas en UTC pelón salen con la hora corrida.** Siempre ISO 8601 con offset.
- **El deploy necesita el sufijo `::modal_app`.** El comando es
  `modal deploy app/main.py::modal_app`. Sin el sufijo Modal busca por defecto una variable
  llamada `app` en el módulo y la nuestra se llama `modal_app` — falla antes de construir
  nada. Lo mismo aplica a `modal run` con el worker outbound.
- **Los archivos de datos NO viajan solos a la imagen de Modal.** `sofia.config.yaml` y
  `prompts/` se agregan explícitamente con `.add_local_file()` y `.add_local_dir()`. Si
  falta uno, el análisis post-llamada revienta **en silencio**: el webhook responde 200 por
  diseño (para que Retell no reintente), así que nada sale a la superficie y parece que
  simplemente no se guardó el resumen. Cada vez que agregues un archivo de datos nuevo al
  proyecto, agrégalo también a la imagen.
- **Un agente recién creado NO tiene versión publicada.** `agent.create` lo deja en un
  borrador v0, y el panel edita ramificando desde la última versión *publicada*: sin ese
  piso, el panel arranca roto con `source_unavailable`. Por eso `provision` publica la v0
  de cada agente. Vale para cualquier flujo que cree agentes nuevos.
- **Importar el número ata solo el inbound.** El binding outbound es una escritura aparte
  sobre el mismo número (`bind_outbound_agent`). Sin ella el worker marca pero Retell no
  tiene agente que atender la llamada que acaba de colocar.

### Del instalador (`scripts/setup.py`)

- **Nada que el instalador produce se le pregunta al usuario.** `MODAL_URL`, los agent ids
  y `DASHBOARD_API_TOKEN` no existen antes del paso que los crea. Pedirlos en la entrevista
  es pedir algo que nadie puede tener todavía.
- **La validación va antes que el provisioning, así que no puede exigir agentes.**
  `validate` prueba la **API key** de Retell cuando aún no hay agent id. Exigir el id ahí
  crea un huevo-y-gallina que mata la instalación antes de empezar.
- **Python 3.12 se verifica de PRIMERO.** Modal no soporta más nuevo, y descubrirlo a media
  instalación obliga a rehacer todo lo anterior bajo otro intérprete. `preflight` corre con
  el Python del sistema (puede ser 3.14) y por eso no importa nada de `app/`: solo stdlib.
