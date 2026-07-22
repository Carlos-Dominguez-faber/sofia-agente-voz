# Instalar — Agente de Voz (Sofía, recepcionista con IA)

## Para ti (miembro de Imperio Digital)

Arrastraste este archivo al chat de tu agente — **Claude Code** (recomendado),
Codex, o cualquier agente que pueda correr comandos en tu terminal. Solo escribe:

> **instálalo**

Y el agente hace todo: te entrevista, valida cada credencial en vivo, crea los dos
agentes de voz en Retell, conecta tu número de Twilio, referencia tu calendario y
pipeline en GoHighLevel, despliega el backend a Modal y el panel de control a Vercel.
El trabajo del agente tarda **~20-30 min** — **pero lee primero los prerrequisitos de
abajo**, porque hay uno que puede tardar días y no depende de ti ni del agente.

### Lo que necesitas tener listo (cuentas)

| Cuenta          | Para qué                                               | Plan            |
| --------------- | ------------------------------------------------------ | --------------- |
| **Retell**      | La voz y los oídos (STT + TTS + turno de conversación) | Pago por minuto |
| **Twilio**      | El número que suena (vía Elastic SIP Trunk)            | Pago por uso    |
| **GoHighLevel** | CRM + calendario + pipeline (la fuente de la verdad)   | Según tu plan   |
| **Anthropic**   | El cerebro (razona en llamada + resumen post-llamada)  | Pago por uso    |
| **Modal**       | El backend Python/FastAPI, 24/7 con URL pública        | Free sirve      |
| **Vercel**      | Hospedaje del panel de control                         | Hobby (gratis)  |

El agente instala lo demás (dependencias de Python, los CLIs de Modal y Vercel).
Cuando termine, el número contesta con Sofía **y** el agente te imprime la URL de tu
panel de control + la contraseña generada para entrar.

### ⚠️ Prerrequisitos que NO se automatizan — léelos de frente

Este agente toca telefonía real, así que hay pasos que ninguna herramienta acelera.
Que nadie se sorprenda a la mitad:

1. **Número de Twilio comprado con el bundle regulatorio APROBADO.** Para México, el
   bundle de identidad tarda **1-3 días hábiles** en que Twilio lo apruebe. **Este es
   el gate que nada acelera.** Sin número aprobado, no hay llamadas — punto. Cómpralo y
   arranca el bundle **antes** de correr la instalación, para que llegue aprobado.

2. **Subcuenta de GoHighLevel ya armada.** Con el calendario **"Valoración dental"** y
   el pipeline **"Nuevos Pacientes"** creados, y **sus IDs a la mano**: Location ID,
   Calendar ID, Pipeline ID y Stage ID. El sistema los **referencia, no los crea**.
   (En Imperio te la regalamos pre-hecha o te aplicamos un snapshot — pídela si no la
   tienes.)

3. **Verificación de identidad de Retell** para llamadas salientes (outbound). Es un
   proceso tipo Persona dentro de Retell; a veces requiere un correo a soporte de
   Retell. **Sin esto, las llamadas salientes se rechazan** (el inbound sí funciona,
   pero el ciclo de devolución de llamadas no). Arráncalo con tiempo.

4. **`modal token new` y `vercel login`** cuando el agente te los pida. Abren el
   navegador para que inicies sesión — **los haces tú**, el agente no puede.

5. **Python 3.12 en tu sistema.** Modal no soporta versiones más nuevas para este
   proyecto, y el entorno local corre en 3.12. Si no lo tienes, el agente te dice cómo
   instalarlo antes de seguir.

> Si todavía no tienes un agente: instala Claude Code (claude.ai/download), ábrelo en
> la carpeta de este proyecto, arrastra este archivo al chat y escribe "instálalo".

---

## Para el agente (Claude Code / Codex) — instrucciones de instalación

Eres el instalador del **Agente de Voz Sofía**. El usuario opera un negocio, no
necesariamente es técnico. Confirma en **español con tuteo de México** cada paso con un
mensaje corto antes de ejecutarlo.

### Reglas generales

- **NO modifiques el código** de `app/` ni de `dashboard/`. Solo corres los scripts y
  comandos de abajo.
- **NUNCA pegues secrets en el chat.** El usuario los pega; el comando `/setup` los
  escribe al `.env`. No los escribas a mano ni los loguees en la conversación.
- **NUNCA commitees** `.env` (ya está en `.gitignore` — no lo fuerces a git).
- Usa los **scripts deterministas de `scripts/`** para lo mecánico. Tú te quedas con lo
  interactivo: pedir las keys, los `login` que abren el navegador, y confirmar.
- **Si algo falla, DETENTE.** Muestra el error **exacto** y explícalo en español simple.
  No avances al siguiente paso hasta resolverlo.
- Confirma cada paso en **español con tuteo de México** antes de ejecutar.

### Pasos en orden

**1. Localiza el proyecto.** Toma el path del `INSTALAR.md` que te arrastraron y haz
`cd` a su carpeta:

```bash
cd "<carpeta donde está este INSTALAR.md>"
```

Si está en `~/Downloads`, pregúntale al usuario si lo mueves a un lugar fijo antes de
seguir (una carpeta de trabajo estable, no la de Descargas).

**2. Preflight de Python — el PRIMER paso, antes de cualquier otra cosa.** Modal solo
soporta **Python 3.12** en este proyecto, y topar con eso a media instalación obliga a
rehacer todo lo anterior. El instalador lo resuelve solo. Córrelo con el Python del
sistema (es el único que hay todavía):

```bash
python3 scripts/setup.py preflight
```

Detecta la versión que hay, localiza Python 3.12, crea el `.venv` con 3.12 e instala las
dependencias. Si **no** encuentra 3.12 se detiene y te da el comando exacto
(`brew install python@3.12` en Mac). Pregúntale al usuario si lo instalas tú; si acepta:

```bash
python3 scripts/setup.py preflight --auto-install
```

Al terminar imprime la ruta del intérprete del entorno. **Usa ESE (`.venv/bin/python`)
en todos los pasos siguientes**, o activa el entorno con
`source .venv/bin/activate`.

Después confirma los dos CLIs:

```bash
modal --version || pip install modal
vercel --version || npm i -g vercel
```

Cuando falten las sesiones, guía al usuario para que las haga **él** (abren el
navegador):

```bash
modal token new              # el usuario inicia sesión en Modal
vercel login                 # el usuario inicia sesión en Vercel
```

**3. Corre `/setup`.** Este es el comando estrella: hace **todo**. Explícale al usuario
qué va a pasar antes de arrancarlo:

- Te **entrevista** para llenar `sofia.config.yaml` (datos del negocio) y te pide las
  credenciales una por una — el usuario las pega, `/setup` las escribe al `.env`, nunca
  al chat.
- **Valida cada credencial en vivo** contra su API real (Retell, Twilio, GHL,
  Anthropic). Si una falla, se detiene ahí con la solución.
- **GHL es referencia, no se crea.** Pide los IDs (Location, Calendar, Pipeline, Stage)
  de la subcuenta **ya armada** y los verifica. No crea calendario ni pipeline.
- **Crea los dos agentes de Retell** (Sofía inbound + Sofía outbound) con `end_call` y
  `update_lead_status` cableados desde el arranque, y **publica** la primera versión de
  cada uno (sin versión publicada, el panel de control no puede editarlos).
- **Conecta Twilio** por Elastic SIP Trunk y ata al número **los dos** agentes.
- **Crea el Modal Secret** `agente-voz-credentials` desde el `.env` (idempotente).
- **Despliega a Modal el backend Y el worker de outbound**, empaquetando
  `sofia.config.yaml` y `prompts/` en la imagen, con el sufijo `::modal_app`.
- **Despliega el panel de control a Vercel** (sube las env vars y hace `vercel --prod`).
- Al terminar, **imprime la URL del panel + la contraseña generada**.

Lo que `/setup` **no** te pregunta, porque lo produce él: la URL del backend en Modal,
los ids de los agentes de Retell y el token del panel. Si un paso te pide uno de esos,
es que falta correr el paso que lo crea.

```bash
/setup
```

Si el bundle de Twilio o la verificación de Retell todavía no están aprobados, `/setup`
te lo dirá al validar — no es un bug tuyo, es el gate externo. Ver troubleshooting.

**4. Corre `/test`.** Verifica los **5 servicios**; cada error viene con su solución,
nunca con un código crudo:

```bash
/test
```

Los 5 son: (1) Retell, (2) Twilio, (3) GoHighLevel (contactos + calendario + pipeline),
(4) Backend (health de Modal), (5) Anthropic. Si alguno falla, resuélvelo antes de
seguir.

**5. 📞 Pídele al usuario que LLAME al número de Twilio.** Un 200 no prueba nada: la
prueba real es la llamada. Pídele que marque, y confirma con él que:

- Sofía **contesta** con voz natural en español.
- **Califica** (motivo, síntoma, urgencia, datos de contacto).
- **Agenda** la cita de valoración — y que aparezca en el calendario de GHL.

Si no contesta o no agenda, revisa `/status` y el troubleshooting. No des la
instalación por buena hasta que una llamada real cierre una cita.

**6. Ofrece `/customize`.** Si el usuario quiere otro nicho (dental →
inmobiliaria, abogados, gimnasio o restaurante) o ajustar voz/comportamiento:

```bash
/customize
```

Recuérdale que el cambio llega **PUBLICADO** a los dos agentes (inbound + outbound), no
en borrador — así el número real lo toma de inmediato. Sugiérele correr `/test` al
terminar.

---

## Si algo falla (troubleshooting)

- **El bundle regulatorio de Twilio está en revisión.** Para México tarda **1-3 días
  hábiles** y **no hay atajo**. Sin el bundle aprobado, el número no puede recibir ni
  hacer llamadas. Espera la aprobación y vuelve a correr `/setup` (es idempotente).

- **Retell rechaza las llamadas salientes.** Es la **verificación de identidad**
  pendiente. Completa el proceso tipo Persona dentro de Retell; si se atora, manda un
  correo a soporte de Retell. El inbound funciona sin esto; el outbound no.

- **Modal falla por versión de Python.** Modal **no soporta 3.13/3.14** para este
  proyecto. Usa **Python 3.12** tanto en el sistema como en el `.venv` (paso 2).

- **El deploy a Modal falla antes de construir.** Falta el sufijo `::modal_app`. El
  comando correcto es `modal deploy app/main.py::modal_app`. Sin el sufijo, Modal busca
  una variable llamada `app` (la nuestra se llama `modal_app`) y truena.

- **El panel de control no despliega o carga vacío en Vercel.** El **Root Directory**
  del proyecto en Vercel debe ser `dashboard` (no la raíz del repo). Ajústalo en
  Vercel → Settings → General → Root Directory y redeploya.

- **Las citas salen con la hora corrida.** El timezone de Cancún es **-05:00 todo el
  año** (no hay horario de verano). Ese offset debe coincidir en `sofia.config.yaml`
  **y** en la configuración de GHL. Si no coincide, las citas se agendan con la hora
  desfasada.

## Actualizar a una versión nueva

```bash
git pull                              # o reemplaza los archivos del proyecto
source .venv/bin/activate
pip install -e .                      # reinstala las deps
modal deploy app/main.py::modal_app   # re-deploy del backend
cd dashboard && vercel --prod         # re-deploy del panel
```

**Nunca** rotes a ciegas las llaves del `.env` ni del Modal Secret `agente-voz-credentials`:
romperías la conexión con los servicios en producción. Solo cámbialas si sabes exactamente
cuál y por qué, y vuelve a correr `/setup` para propagarlas.

## Desinstalar

- **Modal:** borra la app (`agente-voz`) desde el dashboard de Modal o con `modal app stop`.
- **Vercel:** borra el proyecto del panel de control desde el dashboard de Vercel.
- **Retell (opcional):** borra los dos agentes (Sofía inbound + outbound).
- **Twilio (opcional):** libera el número y borra el Elastic SIP Trunk si ya no lo usas.

La **subcuenta de GoHighLevel es del cliente y se queda** — el sistema solo la
referenciaba, nunca guardó estado propio ahí. Fuera de esos servicios en la nube y de
esta carpeta, la instalación no escribe nada en tu sistema.
