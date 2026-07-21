# BLUEPRINT — El dashboard de Sofía

> Este documento explica **por qué** el dashboard está construido como está, para
> alguien que no lo vio construir. Si vas a copiarlo, cambiarlo o defenderlo ante
> un cliente, esto es lo que necesitas entender antes de tocar el código.

---

## 1. Qué es y qué NO es

Es un panel web para el dueño de la clínica. Muestra lo que hizo Sofía —llamadas,
citas, temperatura de pacientes, el funnel— y deja hacer tres cosas: editar cómo
habla Sofía, llamar a un paciente, y ver si los servicios están vivos.

**Lo que NO es, y es la decisión central: no tiene base de datos.** No guarda ni
un registro propio. Cada número que ves lo lee en vivo de dos fuentes:

| Fuente                | Qué sabe                                                                                                                                        |
| --------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| **GoHighLevel (GHL)** | Quién es el paciente, sus citas, su etapa en el pipeline, la temperatura, y lo que el análisis post-llamada concluyó.                           |
| **Retell**            | Qué pasó en el teléfono: cuántas llamadas, cuánto duraron, la transcripción, qué herramientas se dispararon, y el prompt que Sofía usa en vivo. |

Ninguna de las dos, sola, responde la pregunta del dueño. "Ana llamó ayer, agendó,
y sonaba urgente" es **una frase armada con las dos**. Ese cruce —el _join_— es el
corazón del backend del dashboard.

### Por qué sin base de datos

Porque una base de datos propia sería una **segunda copia** de algo que ya vive en
GHL y en Retell. Dos copias de la misma verdad divergen: se actualiza una y no la
otra, y nadie sabe cuál creer. GHL es la única fuente de la verdad para el CRM;
Retell lo es para el historial de llamadas. El dashboard **lee y presenta**, nunca
almacena.

Consecuencia práctica: si quieres saber qué pasó con un paciente, lo ves en GHL, no
en un log del dashboard.

> **La única excepción, y es deliberada:** `app/services/prompt_history.py` guarda
> **una** versión previa del prompt, en un `modal.Dict`. Ver §5. No es dato del CRM
> —es un botón de deshacer para las reglas de seguridad médica— y es la única pieza
> de estado que el backend se permite.

---

## 2. Por qué los endpoints de lectura pertenecen al dashboard, no al agente

El agente de voz (Sofía) ya tenía su backend en Modal: los endpoints de **acción**
que Retell llama en cada llamada —`/create-lead`, `/check-availability`,
`/book-appointment`, `/update-lead-status`— más el webhook `/retell-webhook`.

Los endpoints de **lectura** del dashboard (`/dashboard/metrics`, `/dashboard/calls`,
etc.) viven en el **mismo** FastAPI, pero en un archivo aparte
(`app/dashboard_api.py`), montado con una línea (`include_router`). Dos razones, y
la segunda es la que importa:

1. **Los de acción son sagrados.** Retell los usa en llamadas reales, en producción.
   Nada que se agregue para el dashboard debe poder alterarlos. Separarlos en su
   propio módulo hace que el riesgo sea imposible por construcción.
2. **Tienen modelos de seguridad distintos.** Los de lectura exigen un token
   compartido (§3). Los de acción se autentican distinto —el webhook verifica
   firma, y las tools escriben datos que el llamante ya proveyó—. Mezclar los dos en
   un archivo es cómo una ruta acaba del lado equivocado del candado.

La lógica de negocio vive en `app/services/`, no en los handlers. Por eso el
dashboard puede reusar exactamente las mismas reglas que el agente, sin duplicar
una sola.

---

## 3. Autenticación: dos capas, dos puertas distintas

Este es el punto que más te va a servir si copias esto. **Un token no basta.** Hay
dos problemas de seguridad distintos y cada uno necesita su propia cerradura.

### Capa 1 — el token del backend

La URL de Modal es **pública**. Tiene que serlo: Retell la llama desde internet en
cada turno de cada llamada. Los endpoints de acción están a salvo en una URL
pública —necesitan firma o solo escriben datos que ya recibieron—.

Los endpoints de lectura son otra cosa: devuelven transcripciones, resúmenes y
teléfonos de pacientes reales. En una URL abierta, cualquiera que aprenda la
dirección lee el historial médico de la clínica.

Solución: **un token compartido en el header `Authorization: Bearer`**, validado
contra el Modal Secret con comparación en tiempo constante (`secrets.compare_digest`
— un `==` normal filtra el token un carácter a la vez por el tiempo de respuesta).
`/health` se queda público porque Modal lo sondea y no expone nada.

**El token nunca toca el navegador.** El panel Next.js lo guarda en una variable de
entorno de solo-servidor y **proxea** cada petición: el navegador habla con
`/api/*` del propio Next, y un handler del lado del servidor le agrega el token. Un
token con prefijo `NEXT_PUBLIC_` es un token publicado, se llame como se llame.

### Capa 2 — la contraseña del panel

Con solo la capa 1, el token está a salvo pero **el panel no**: quien tenga la URL
de Vercel abre resúmenes de pacientes reales sin credencial alguna. Y una URL no es
un secreto —se comparte, se pega en un chat, se indexa—.

Solución: **una contraseña**, que se cambia por una cookie de sesión `httpOnly`
firmada con HMAC. El middleware (`proxy.ts` en Next 16) la verifica antes de cada
petición, con _default-deny_: solo pasan `/login`, los endpoints de auth y los
assets estáticos. Una ruta nueva queda protegida en el momento en que existe.

**Un cliente, una contraseña. Sin tabla de usuarios, sin roles.** Eso —roles y
usuarios— es el multiusuario que este proyecto deliberadamente NO construye. Esto es
una sola cerradura en una sola puerta.

### Por qué el proxy usa handlers explícitos, no un comodín

Esta es la decisión más importante del dashboard. Un proxy comodín —
`/api/backend/[...path]` que reenvía cualquier ruta con el token adjunto— le daría a
cualquier pestaña con sesión (o a un script corriendo en una) la capacidad de
alcanzar **todos** los endpoints del backend, incluidos los de acción que Retell usa
en vivo: `/create-lead`, `/book-appointment`, `/update-lead-status`.

**Un panel de solo lectura que puede escribir citas en el calendario real no es un
panel de solo lectura.**

Por eso cada operación permitida tiene su propio handler explícito
(`/api/agent/prompt`, `/api/outbound/call`, `/api/calls/[callId]`…). Lo que no está
escrito no se puede alcanzar, y agregar una capacidad es una decisión visible, no un
accidente de ruteo.

---

## 4. De dónde sale cada número

| Métrica                                      | Fuente   | Nota                                                                                              |
| -------------------------------------------- | -------- | ------------------------------------------------------------------------------------------------- |
| Llamadas totales, duración promedio          | Retell   | Filtrado por `agent_id` — sin ese filtro, una cuenta compartida mezcla llamadas de otros agentes. |
| Transcripción, tools disparadas, prompt vivo | Retell   |                                                                                                   |
| Funnel por etapa                             | GHL      | Oportunidades del pipeline, agrupadas por stage.                                                  |
| Temperatura hot/warm/cold                    | GHL      | Conteo por tag.                                                                                   |
| Resumen, interés, urgencia, probabilidad     | GHL      | Los custom fields que escribe el análisis post-llamada.                                           |
| **Citas agendadas por Sofía**                | **join** | Ver abajo.                                                                                        |
| **Tasa de éxito**                            | join     | citas ÷ llamadas totales.                                                                         |
| Llamadas recientes con nombre                | join     | Retell da la llamada y el teléfono; GHL da el nombre.                                             |

**La llave del join es el teléfono en E.164**, recuperado de los argumentos que
Sofía pasó a sus propias tools durante la llamada (`app/services/call_parsing.py`).
No hay tabla que mapee llamada → contacto, porque eso sería estado.

### "Citas agendadas por Sofía", no todas las del calendario

El número de citas cuenta **`book_appointment` exitosos en Retell**, no los eventos
del calendario de GHL. ¿Por qué? El calendario también tiene las citas que la
recepcionista humana agendó a mano. Contar esas le acreditaría a Sofía trabajo que
no hizo —e inflaría la tasa de éxito, que es justo el número que justifica lo que
cuesta—. La etiqueta en la UI lo dice explícito: _"Citas agendadas por Sofía"_.

### No hay tarjeta de costos

Se consideró y quedó fuera. Retell expone su costo por llamada, pero **Twilio y
Anthropic se facturan aparte** y nadie los suma. Una tarjeta de "costos" con solo una
de las tres fuentes miente por omisión: el dueño la leería como el costo total de
operar a Sofía. Cuando exista una fuente que sume las tres, entra.

---

## 5. El prompt: quién manda, y cómo se protege lo que no se toca

### El ciclo de vida (por qué el YAML y el panel no se contradicen)

Hay una tensión aparente que vas a notar:

- Durante el **desarrollo**, el prompt vive en `prompts/dental.yaml` y se empuja a
  Retell por API. Editarlo en la consola de Retell desincroniza el repo.
- En **producción**, el cliente lo edita desde el panel, y eso escribe a Retell.

No es contradicción, es ciclo de vida: **el YAML es la semilla de instalación**
—lo que `/setup` pone la primera vez— y **una vez instalado, Retell es la verdad y
el panel es la única puerta.** El endpoint `GET /dashboard/agent/prompt` lee el
prompt vivo de Retell, no del YAML.

### La protección de los guardrails de seguridad

El prompt contiene la sección 11, **Safety & Scope Guardrails**: Sofía nunca
diagnostica, nunca recomienda medicamentos, nunca confirma una cita que el sistema no
creó. Esas reglas son la línea que separa a una recepcionista de un acto médico sin
licencia.

El panel deja editar el prompt —cambiar un precio, meter una promoción—. Pero un
cliente editando un precio no tiene ninguna razón para borrar "nunca diagnostica", y
un `textarea` no lo sabe: select-all, pegar, guardar, y los guardrails desaparecieron
de una línea telefónica en vivo, sin aviso.

**La solución: el bloque no es editable, es propiedad del servidor.** El backend le
entrega al panel el prompt con la sección 11 reemplazada por un marcador
(`<<< REGLAS DE SEGURIDAD — NO EDITABLES >>>`), y vuelve a poner el bloque canónico
—el del repo, revisado— al guardar. El cliente edita todo lo que rodea al marcador y
no puede borrar lo que nunca estuvo en su textarea. Si el marcador se borra de todas
formas, `PUT` se rechaza con 422 y el mensaje del backend.

> **Se rechaza, no se advierte.** La diferencia es todo el punto:
> **una advertencia que se puede ignorar no es una barrera.**

### El deshacer (la excepción al "sin estado")

`PUT /dashboard/agent/prompt` guarda la versión anterior en un `modal.Dict` antes de
publicar la nueva. Una sola versión —es un botón de deshacer, no un historial—.

El motivo de fondo justifica romper la regla de "sin estado propio": el prompt
contiene los guardrails de seguridad médica. Si un cliente publica una edición que
rompe a Sofía, "restaurar la versión anterior" tiene que ser un clic, no un ticket de
soporte mientras una línea en vivo sigue atendiendo pacientes mal. No es dato del CRM
—es un string del dominio de Retell, que Retell no versiona— así que no viola "GHL es
la fuente de la verdad".

---

## 6. La honestidad ante el error (por qué nunca ves un cero falso)

Regla que no se negocia: **cuando una fuente no responde, eso viaja a la UI como una
fuente caída, nunca como un cero.**

El dueño de una clínica lee un `0` como "Sofía no trabajó hoy". Esa mentira —tan
cómoda— es más cara que un mensaje de error. Así que si Retell no contesta, la
tarjeta de métricas dice "Dato no disponible" con el motivo, en el mismo espacio donde
iría el número. El componente `SourceState.tsx` hace cumplir esto en el front; los
endpoints devuelven `503` con la fuente nombrada, no un `200` con ceros.

Distinguir "no hubo llamadas" (dato real, la consulta respondió) de "Retell no
respondió" (fuente caída) es la diferencia entre las que el dueño debe ignorar y las
que debe atender.

---

## 7. Dos gotchas de despliegue que fallan en silencio

Los dos son de la misma familia: **fallan sin ruido y mienten sobre quién tuvo la
culpa.** Anótalos, porque cualquiera que extienda esto se los va a topar.

### Gotcha 1 — los archivos de datos no viajan a la imagen de Modal

La imagen de Modal solo incluye lo que agregas **explícitamente**. Al principio
copiaba `sofia.config.yaml` pero **no** `prompts/`. Consecuencia: el análisis
post-llamada, que lee `prompts/<industria>.yaml` en cada `call_ended`, lanzaba una
excepción dentro del webhook.

**Y aquí está lo que lo escondió:** el webhook responde `200` por diseño (para que
Retell no reintente en cada llamada en curso), y `process_call_ended` corre en
background. Así que la excepción solo aterrizaba en un `LOG.error` que nadie miraba.
El análisis post-llamada estuvo roto en producción **desde que existía** y nada lo
delataba. Lo que se veía funcionando en las clases era el análisis corrido en local a
mano.

El arreglo es una línea —`.add_local_dir("prompts", "/root/prompts")`— pero la
lección no es la línea: es que **un webhook que siempre responde 200 y trabaja en
background esconde todos sus fallos.** Si agregas un archivo de datos nuevo (otro
nicho, otro YAML), tienes que agregarlo a la imagen, y no vas a tener un error que te
avise si se te olvida.

### Gotcha 2 — el sufijo del comando de deploy

El comando es:

```bash
modal deploy app/main.py::modal_app
```

El sufijo `::modal_app` es **obligatorio**. Sin él, Modal busca una variable llamada
`app` y la nuestra se llama `modal_app`; falla antes de construir nada. Lo mismo
aplicará a `modal run` cuando se despliegue el worker outbound.

---

## 8. Estructura del código

```
app/                              (backend, sobre Modal)
├── main.py                       endpoints de ACCIÓN (Retell) + monta el router del dashboard
├── dashboard_api.py              los 10 endpoints de LECTURA, bajo /dashboard, tras el token
├── auth.py                       la validación del token (capa 1)
└── services/
    ├── dashboard_service.py      los joins Retell↔GHL y las métricas
    ├── ghl_read_service.py       lecturas de GHL (ghl_service.py, el de escritura, intacto)
    ├── call_parsing.py           parseo de transcripción y tool-calls (compartido con main.py)
    ├── prompt_guard.py           la protección de la sección 11
    ├── prompt_history.py         el deshacer (la excepción al "sin estado")
    └── retell_service.py         lecturas de Retell + provisioning

dashboard/                        (panel, Next.js)
└── src/
    ├── proxy.ts                  el gate de la capa 2 (default-deny)
    ├── config/branding.ts        marca por cliente — UN archivo
    ├── lib/api.ts                el ÚNICO que habla con el backend; adjunta el token server-side
    ├── app/api/…/route.ts        los proxies explícitos (NO comodín)
    └── components/               las 7 secciones + SourceState (el que impide el cero falso)
```

Los endpoints del backend, para referencia:

```
GET  /dashboard/metrics              GET  /dashboard/calls
GET  /dashboard/funnel               GET  /dashboard/calls/{call_id}
GET  /dashboard/leads/temperature    GET  /dashboard/agent/prompt
GET  /dashboard/services/status      PUT  /dashboard/agent/prompt
POST /dashboard/outbound/call        POST /dashboard/agent/prompt/undo
```
