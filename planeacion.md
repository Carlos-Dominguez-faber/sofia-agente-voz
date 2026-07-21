---
proyecto: agente-voz-ghl
caso: Clínica Dental "Sonrisa Perfecta" · agente "Sofía"
estado: planeación — NADA de código todavía
actualizado: 2026-07-20
---

# Planeación — Agente de voz "Sofía"

> **Fuente de verdad del proyecto.** Este documento se escribe **antes** de construir.
> El alcance vive en [BRIEF.md](BRIEF.md), el detalle técnico en
> [BRIEF-TECNICO.md](BRIEF-TECNICO.md), y el contexto operativo en [CLAUDE.md](CLAUDE.md).
> Aquí queda **la decisión**: qué se construye, en qué orden y contra qué IDs reales.
>
> Lo que está marcado **⚠️ PENDIENTE** son huecos reales, no adornos. Hay que cerrarlos
> antes de escribir el código que depende de ellos.

---

## 1. Negocio

| | |
|---|---|
| **Nombre** | Clínica Dental "Sonrisa Perfecta" |
| **Sitio** | `clinicasonrisaperfecta.site` |
| **Plaza** | Cancún / CDMX |
| **Timezone** | `America/Cancun` |
| **País** | MX |

> **Verificado contra GHL** (`GET /locations/{id}`): la Location existe, se llama
> "Clinica Dental Sonrisa Perfecta", timezone `America/Cancun`, país MX, sitio
> `clinicasonrisaperfecta.site`. El plan no está construido sobre supuestos.

### El agente

**Sofía**, recepcionista de la clínica. Tono **amable, empático y tranquilizador** — mucha
gente que llama a un dentista llama con miedo o con dolor, y ese es el estado emocional
que Sofía tiene que sostener.

**Su trabajo, exactamente tres cosas:**

1. **Contestar 24/7.**
2. **Calificar** a quien llama.
3. **Agendar una cita de valoración.**

**Lo que Sofía NO hace, y es tan importante como lo que sí:**

- **Nunca diagnostica.** Ante cualquier pregunta clínica: *"el doctor te valora en la cita"*.
  No es timidez del producto — es la línea que separa una recepcionista de un acto médico
  sin licencia.
- **Los precios son siempre aproximados.** Nunca los presenta como cotización cerrada. El
  precio final sale de la valoración.

---

## 2. Preguntas de calificación (en orden)

El orden importa: primero se entiende **por qué llama**, y solo al final se piden los datos.
Pedir el teléfono antes de haber escuchado el problema es lo que hace que la gente cuelgue.

| # | Pregunta | Qué se busca |
|---|----------|--------------|
| **1** | **Motivo de la llamada** | Abre la conversación. Es lo que la persona quiere contar. |
| **2** | **Síntoma y urgencia** | **DOLOR · HINCHAZÓN · SANGRADO → es urgencia, priorizar.** |
| **3** | **Tratamiento de interés** | Ancla la conversación al catálogo del KB. |
| **4** | **Datos de contacto** | Nombre, teléfono, correo. Al final, ya con confianza construida. |

### La regla de urgencia

Si aparece **dolor, hinchazón o sangrado**, Sofía cambia de modo: deja de vender tratamiento
y prioriza el agendado. Ofrece el hueco disponible más cercano y marca el contacto como
urgencia.

Sigue sin diagnosticar. "Priorizar" significa **agendar antes**, no interpretar el síntoma.

### Pacing (lo que hace que no suene a robot)

- Teléfonos **dígito a dígito**, confirmando al final. Correos **deletreados**.
- **Pausa de 3 segundos** tras consultar disponibilidad, antes de ofrecer horarios.
- Horarios dichos **una sola vez**, en formato hablado ("el martes a las cuatro de la tarde").
- **Precisión por encima de velocidad.** Cuando dude, frena.

---

## 3. Herramientas de Sofía y su mapeo a GHL

> **Las herramientas van ANTES que el agente.** De nada sirve una recepcionista brillante sin
> acceso al sistema de citas: primero las manos, luego la voz. Este es el orden de
> construcción, no una lista de deseos.

**Base de la API:** `https://services.leadconnectorhq.com` · header `Version: 2021-07-28` ·
`Authorization: Bearer <Private Integration Token>` · todo scoped a la **Location**.

| # | Herramienta | Endpoint GHL | Notas duras |
|---|---|---|---|
| 1 | **Crear contacto** | `POST /contacts/upsert` | Teléfono **E.164**. Idempotente por teléfono — nunca duplica. |
| 2 | **Consultar disponibilidad** | `GET /calendars/{calendarId}/free-slots` | Fechas en **epoch ms**, `timezone` IANA, máx. 31 días. **Filtrar `traceId`** de la respuesta. |
| 3 | **Agendar cita** | `POST /calendars/events/appointments` | **ISO 8601 con offset**, no UTC pelón. |
| 4 | **Crear/mover oportunidad** | `POST /opportunities/` + `POST /contacts/:id/tags` | La oportunidad al stage "Cita Agendada"; los tags llevan la temperatura. |
| 5 | **Resumen post-llamada** | Claude sobre la transcripción → nota + custom fields | Interés 1-10, urgencia, probabilidad de asistir. |

### La regla que no se negocia

**Si GHL falla, el agente NO miente.** El endpoint devuelve un error honesto y Sofía ofrece
seguimiento humano: *"déjame confirmártelo, en un momento te contacta una persona del equipo"*.

Nunca un "ya quedó agendado" falso. El costo de esa mentira es concreto: el paciente cuelga
creyendo que tiene cita, no llega nadie a recibirlo, y pierdes al cliente **y** la confianza.
Un error honesto cuesta una llamada de seguimiento; una cita fantasma cuesta el paciente.

---

## 4. Los IDs reales de GHL

Consultados en vivo contra la Location. **Estos ya no son placeholders.**

| Recurso | ID | Estado |
|---|---|---|
| **Location** | `x8Wqh0mvjN31MqrUHtby` | ✅ verificado |
| **Pipeline** "Nuevos Pacientes" | `AJeNrg03SbkJ5JbhRs0l` | ✅ verificado |
| **Stage** "Cita Agendada" | `072d84e4-5829-46dd-b749-2e61d57a836b` | ✅ verificado |
| **Calendario** "Limpieza Dental" | `CAyujBZ84e9YzfVl4EMV` | ✅ activo — ver nota ⚠️ |

### El pipeline completo

Solo existe **un** pipeline en la Location, y las citas entran en la posición 2:

```
0. New Lead  →  1. Engagement  →  2. ★ Cita Agendada  →  3. Asistió a Cita
                                                       ↘  4. No Asistió  →  (outbound lo recupera)
5. Tratamiento en Proceso  →  6. Cliente Ganado
```

"No Asistió" es la etapa que alimenta el **worker outbound**: los no-shows se recuperan desde
ahí.

### ⚠️ PENDIENTE — el calendario no se llama "Valoración dental"

El brief asume un calendario "Valoración dental". **No existe.** En la Location hay dos:

| Calendario | ID | Estado |
|---|---|---|
| Limpieza Dental | `CAyujBZ84e9YzfVl4EMV` | activo — **el que usamos hoy** |
| Dental Consultation | `CKj17k1fVAiGmnz8DpsD` | **inactivo** |

**Decisión tomada:** se queda "Limpieza Dental". Funciona y devuelve slots hoy.

**La deuda que eso genera:** las citas de valoración caen en la agenda de limpiezas, así que
los dos tipos de cita quedan mezclados en un solo calendario. Cuando el volumen crezca, esto
se separa. Anotado como deuda consciente, no como descuido.

**Disponibilidad verificada:** 18 slots por día, de 08:00 a 17:00, 4 días con hueco en la
próxima semana.

### ⚠️ PENDIENTE — el offset de Cancún es −05:00, NO −06:00

Los briefs usan `-06:00` como ejemplo (hora del centro de México). **Cancún es UTC−5 todo el
año, sin horario de verano.** `free-slots` lo confirma: `2026-07-21T08:00:00-05:00`.

**Implicación para el código:** `book_appointment` **deriva el offset del `timezone` del
config**, nunca lo hardcodea. Un offset fijo mete todas las citas con una hora de diferencia
— que es exactamente el gotcha que el brief ya advierte, solo que con el número equivocado.

---

## 5. Custom fields — lo que hay y lo que falta

Consultado en vivo (`GET /locations/{id}/customFields`). **Existen cuatro:**

| Field key | Nombre | Tipo | ¿Lo usamos? |
|---|---|---|---|
| `contact.reason_for_visit` | Reason for Visit | TEXT | ✅ **sí** — motivo de la llamada |
| `contact.notas_clinicas` | Notas Clínicas | LARGE_TEXT | ❌ **no** — ver abajo |
| `contact.fecha_de_ultima_visita` | Fecha de Última Visita | DATE | ❌ no (lo llena la clínica) |
| `contact.fecha_vencimiento_cupon` | fecha_vencimiento_cupon | DATE | ❌ no (marketing) |

### ⚠️ PENDIENTE — faltan los tres campos del post-llamada

Los campos que el análisis post-llamada necesita **no existen todavía**. Hay que crearlos
(los crea `/setup`, o a mano en GHL) antes de construir el paso 3 del orden de construcción:

| Field key propuesto | Qué guarda | Tipo |
|---|---|---|
| `contact.interes_score` | Interés 1-10 | NUMERICAL |
| `contact.nivel_urgencia` | urgente / normal / baja | TEXT |
| `contact.probabilidad_asistir` | Probabilidad de asistir 1-10 | NUMERICAL |
| `contact.resumen_llamada` | Resumen de Claude | LARGE_TEXT |

### Por qué el resumen NO va en `notas_clinicas`

Existe un campo LARGE_TEXT llamado "Notas Clínicas" y es tentador reusarlo para el resumen de
la llamada. **No hay que hacerlo.**

Ese campo es el registro clínico del paciente: lo escribe el doctor y es lo que se consulta
para tomar decisiones de tratamiento. El resumen de Sofía es output de un modelo sobre una
transcripción de teléfono. Mezclarlos significa que, en algún momento, alguien va a leer una
inferencia de IA creyendo que es una observación clínica.

Va en `contact.resumen_llamada`, separado. Sofía nunca escribe en el expediente clínico —
es la misma línea del "nunca diagnostica", aplicada a los datos.

### Temperatura del lead (tags)

`hot` / `warm` / `cold`, vía `POST /contacts/:id/tags`. Criterio a fijar con el análisis
post-llamada — preliminar: urgencia + interés alto → `hot`.

---

## 6. Decisión de arquitectura

**Todo vive en GoHighLevel.** Contactos, calendario y pipeline en una sola subcuenta
(Location). Sin base de datos aparte, sin capa de automatización extra.

**GHL es la única fuente de la verdad.** El backend no guarda estado propio: lee y escribe
en GHL.

**Por qué:**

- **Menos piezas.** Un proveedor por capa, a propósito.
- **Menos integraciones que mantener.** Cada sincronización entre dos sistemas es un bug
  futuro esperando su turno.
- **En la comunidad regalamos cuentas de GHL.** Quien recibe el sistema ya tiene el CRM —
  no hay que pedirle que contrate nada más.

**Consecuencia práctica:** si quieres saber qué pasó con un paciente, lo ves en GHL, no en un
log.

**Consecuencia para el dashboard:** solo **lee** — de GHL y del backend en Modal. No tiene
base propia y no hay nada que sincronizar.

---

## 7. Por qué lo dental simplifica

**No hay inventario que buscar.** Es la diferencia estructural con una inmobiliaria: ahí cada
llamada requiere consultar propiedades disponibles contra una base de datos que cambia todos
los días, y eso obliga a un endpoint `search-products` y a una fuente de datos viva.

En dental, los tratamientos son **una lista fija y corta**. Viven en el **Knowledge Base del
prompt** y en `sofia.config.yaml` — **no** en una base de datos consultable.

| Tratamiento | Precio aproximado |
|---|---|
| Limpieza | ~$800 |
| Blanqueamiento | ~$3,500 |
| Ortodoncia | ~$500 **mensuales** |
| Implante | ~$15,000 |
| Endodoncia | ~$4,000 |

Todos **aproximados**, siempre. El precio final sale de la valoración.

**Lo que esto nos ahorra:** un endpoint menos, una fuente de datos menos, y una clase entera
de bugs (inventario desactualizado, race conditions, "te ofrecí algo que ya no está").

### ⚠️ PENDIENTE — el precio de la cita de valoración

El brief lista "ortodoncia ~$500 mensuales". En el encargo apareció como
"ortodoncia/valoración ~$500", que junta dos cosas distintas: la mensualidad de ortodoncia y
el costo de la valoración misma.

**No lo invento.** La cita de valoración es *el producto que Sofía vende en cada llamada* —
si es gratis, es el argumento de cierre más fuerte que tiene; si cuesta, tiene que decirlo
antes de agendar. Hay que confirmarlo. En el YAML queda como `PENDIENTE_CONFIRMAR`.

---

## 8. Orden de construcción

Las herramientas antes que el agente. Nada de esto se construye todavía.

| # | Paso | Depende de |
|---|---|---|
| 1 | **Capa GHL** (`ghl_service.py`) + `test_connection` | ✅ nada — se puede empezar ya |
| 2 | **Endpoints** de `main.py` + webhooks, con el error honesto en booking | 1 |
| 3 | **Análisis post-llamada** → nota + custom fields | ⚠️ crear los 4 custom fields |
| 4 | **Precargar el negocio** (`sofia.config.yaml` + `prompts/dental.yaml`) | ⚠️ precio de valoración |
| 5 | **Skills** `/setup` `/test` `/customize` `/status` | 1-4 |
| 6 | **Deploy a Modal** + agentes de Retell | ⚠️ instalar Modal CLI |
| 7 | **Dashboard** | todo lo anterior |

### Estado de credenciales (verificado en vivo)

| Servicio | Estado |
|---|---|
| GHL — `test_connection` + `free-slots` | ✅ HTTP 200 |
| Retell | ✅ HTTP 200 |
| Twilio | ✅ HTTP 200 |
| Anthropic | ✅ HTTP 200 |
| **Modal** | ⚠️ **CLI no instalado** — `pip install modal` + `modal token new` |

---

## 9. Los pendientes, en un solo lugar

**Cerrados** (2026-07-20 / 21): precio de la valoración ($500 reembolsable) ·
los 4 custom fields creados en GHL · Modal CLI instalado y autenticado ·
horario de atención (lunes a viernes 8:00–17:00, alineado con la agenda real).

**Abiertos:**

| # | Pendiente | Bloquea |
|---|---|---|
| 1 | **`voice_id` de ElevenLabs** es-MX | hoy corre con `retell-Andrea` (voz de plataforma, mexicana). Solo si se quiere cambiar de proveedor. |
| 2 | **Criterio de tags** hot/warm/cold | nada — Claude ya los asigna; falta fijar el criterio explícito en el config |
| 3 | **Ventana horaria del outbound** | paso 6 (worker) |
| 4 | **Prompt outbound** y los otros 4 nichos | paso 6 y la reventa del sistema |
| 5 | *Deuda consciente:* separar el calendario de valoración del de limpiezas | cuando crezca el volumen |

---

## 10. Estado al cierre de la sesión del 2026-07-21

**Construido y desplegado.** Pasos 1, 2, 3 y 6 (parcial) del orden de construcción.

| Pieza | Estado |
|---|---|
| `ghl_service.py` | ✅ 4 funciones + tags, notas, custom fields, `test_connection` |
| `main.py` | ✅ 6 endpoints, desplegado en Modal |
| `anthropic_service.py` | ✅ análisis post-llamada con `claude-opus-4-8`, salida validada |
| `retell_service.py` | ✅ crea y actualiza el LLM y el agente desde código |
| Agente Sofía (inbound) | ✅ `agent_f59dfb66edca38e467eac0b003` · LLM `llm_089a90cd550f162214e1646cf24d` |
| URL pública | `https://contacto-66951--agente-voz-ghl-fastapi-app.modal.run` |
| Twilio ↔ Retell | ⬜ **no conectado** — el número aún no enruta al agente |
| Worker outbound | ⬜ no construido (paso 6) |
| Dashboard | ⬜ no construido (paso 7) |

**Verificado en llamadas reales:** califica, consulta la agenda, agenda la cita,
crea contacto y oportunidad en GHL, y escribe resumen + 3 scores + tag de
temperatura tras colgar.

### Dos fallos silenciosos que costaron caro — no repetirlos

1. **GHL descarta `{key, field_value}` en custom fields.** Responde 200/201 y no
   escribe nada. La forma que persiste es `{id, value}`, resolviendo el id desde
   la Location. Afectaba también a `upsert_contact`, así que `/create-lead`
   estuvo descartando el `reason` sin que nada lo indicara.
2. **`prompts/` no viajaba a la imagen de Modal.** El análisis post-llamada lo
   lee en cada `call_ended`; en producción habría reventado sin dejar rastro,
   porque el webhook responde 200 por diseño para que Retell no reintente.

La lección común: **un 2xx no es evidencia de que algo pasó.** Ambos salieron
solo por leer el recurso de vuelta. Y la variante de prompt: **la agenda manda,
no la instrucción** — Sofía ofrecía las 8:00 pese a la regla del prompt, porque
el backend se las entregaba como disponibles.

### Siguiente paso natural

Conectar el número de Twilio al agente por Elastic SIP Trunk, para pasar de
llamadas web de prueba a llamadas telefónicas reales.
