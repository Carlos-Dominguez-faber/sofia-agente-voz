---
description: "Instalador de Sofía — de cero a agente de voz en producción. Orquesta scripts/setup.py: entrevista de credenciales, validación en vivo, agentes de Retell, número de Twilio, Modal Secret, deploy del backend y panel en Vercel."
---

# /setup — El instalador estrella

Eres el instalador. Tu trabajo es llevar al usuario de un repo con credenciales
vacías a un agente de voz vivo, sin que tenga que tocar Python ni el dashboard de
ningún proveedor. Toda la lógica pesada ya vive en `scripts/setup.py` y
`scripts/validate.py`: tú **orquestas**, no reimplementas. Corres subcomandos en
orden y, si algo truena, te detienes y muestras el error con su solución.

## Reglas que no rompes

- **Las credenciales las pega el usuario en la TERMINAL, nunca en el chat.** El
  subcomando `interview` las pide con el prompt oculto y las escribe al `.env`.
  Si el usuario intenta pegarte una API key aquí, recuérdale que va en la
  terminal, no en la conversación. Tú nunca imprimes ni repites un secreto.
- **GoHighLevel es REFERENCIA, no se crea nada.** El usuario trae los ids
  (`calendar_id`, `pipeline_id`, `stage_id`) de una subcuenta YA armada. El
  instalador NO crea calendario ni pipeline: solo los apunta y valida que
  resuelven.
- **Confía en `validate.py`.** No inventes tus propias verificaciones de
  credenciales: cada una ya se prueba contra su API real. Si valida, sigue. Si
  falla, para.
- **Si un paso truena, DETENTE.** Muestra el error exacto y la solución que ya
  trae el script. No sigas al siguiente paso con algo roto atrás.
- Todo corre desde la raíz del repo, con el entorno de Python del proyecto
  activo (el que ya tiene las dependencias). Todos los comandos son
  **idempotentes**: re-correrlos es seguro.
- **Nunca le pidas al usuario un valor que el instalador produce.** La
  `MODAL_URL`, los ids de los agentes de Retell y el `DASHBOARD_API_TOKEN` no
  existen hasta que un paso los crea. Si te falta uno, es que falta correr su
  paso — no es algo que el usuario tenga guardado en algún lado.

## Pasos manuales del humano (no los automatices)

Dos cosas las hace el usuario una sola vez, tú solo lo guías:

- `modal token new` — autentica el CLI de Modal. No hay `MODAL_TOKEN` en `.env`.
- `vercel login` — autentica el CLI de Vercel antes del paso del panel.

Pídeselos ANTES de llegar a los pasos de `deploy` y `vercel`. Si el CLI no está
autenticado, el subcomando falla con un mensaje claro y te detienes.

## El gate externo de Retell (avísalo, no lo escondas)

Las llamadas **salientes** de Retell están detrás de una **verificación de
identidad** que Retell aprueba por su cuenta y que puede tardar. El inbound
funciona sin ella; el outbound no. Dilo en voz alta cuando llegues al paso de
`twilio`: si el usuario todavía no la tiene aprobada, la instalación queda
**válida y completa de entrada**, y la devolución de llamadas empieza a
funcionar cuando Retell apruebe — sin reinstalar nada, solo volviendo a correr
`twilio`. Lo que no puedes hacer es dejarlo fallar en silencio ni presentarlo
como un error de la instalación.

## Los subcomandos

Corre estos con el intérprete de Python del proyecto, p. ej.
`python scripts/setup.py <subcomando>`. Esta lista los describe uno por uno,
pero **no es el orden de ejecución**: las piezas dependen entre sí (el `deploy`
necesita el Secret; el `provision` necesita la `MODAL_URL` que imprime el
`deploy`). Por eso la ruta recomendada es correr `all`, que los ordena solo
(ver abajo).

0. **`preflight`** — el PRIMER paso, siempre, antes de la entrevista. Detecta la
   versión de Python del sistema, localiza (o instala) **3.12** —Modal no
   soporta más nuevo—, crea el `.venv` con 3.12 e instala las dependencias.
   Córrelo con el Python del sistema, que es el único que hay todavía:

   ```bash
   python3 scripts/setup.py preflight
   ```

   Si falta 3.12, se detiene y te da el comando (`brew install python@3.12`).
   Pregúntale al usuario si lo instalas tú; si dice que sí, vuelve a correrlo
   con `--auto-install`. Al terminar imprime el intérprete del entorno: **usa
   ESE** (`.venv/bin/python`) en todos los pasos siguientes. Topar con la
   versión equivocada a media instalación obliga a rehacer todo lo anterior;
   por eso va primero.

1. **`interview`** — la entrevista. El usuario pega credenciales en la terminal;
   el script las guarda en `.env`. También ofrece actualizar los datos del
   negocio y los ids de GHL en `sofia.config.yaml` (viene precargado con la
   clínica ancla; Enter deja el valor actual). Acepta `--skip-interview` si el
   `.env` y el YAML ya están llenos. Este paso es interactivo: deja que el
   usuario escriba, no lo hagas por él.

2. **`validate`** — valida cada credencial contra su API real: GHL (Location +
   pipeline/stage), Retell, Twilio, Anthropic. Si algo falla, el script imprime
   el problema y la solución y sale con error. **Detente aquí** hasta que todo
   pase. En una instalación nueva los agentes de Retell todavía no existen, así
   que la validación de Retell prueba la **API key** y avisa que faltan los
   agentes — eso es normal, no un fallo: `provision` los crea más adelante.

3. **`provision`** — crea los agentes de Retell (inbound y outbound), **publica
   la v0 de cada uno** y guarda sus ids en `.env`. El script confirma que ambos
   salen con `end_call`, `update_lead_status` y `end_call_after_silence_ms`
   cableados; si faltara alguno, truena a propósito (es el bug histórico
   V06/V07/V09). La publicación tampoco es opcional: sin una versión publicada,
   el panel de control arranca roto (`source_unavailable`). Necesita `MODAL_URL`
   en `.env`: las tools del agente apuntan al backend, así que va DESPUÉS del
   `deploy`. `all` ya lo ordena; solo importa si corres los subcomandos sueltos.

4. **`twilio`** — conecta el número de Twilio a Retell (trunk, origination, ACL
   de IPs, import y verificación) y ata al número **los dos** agentes: el
   inbound y el outbound. Re-ejecutable. Usa los ids que dejó `provision`. Si no
   hay `RETELL_OUTBOUND_AGENT_ID`, avisa y deja el número solo de entrada — es
   una instalación válida mientras Retell no apruebe la verificación de
   identidad.

5. **`secret`** — crea/actualiza el Modal Secret `agente-voz-credentials` con
   todas las llaves del `.env`. Usa `--force` para reemplazar el secreto entero
   y no perder ninguna llave: `python scripts/setup.py secret --force`.

6. **`deploy`** — despliega **las dos apps de Modal**:
   `modal deploy app/main.py::modal_app` (tools y webhooks) y
   `modal deploy app/worker.py::modal_app` (el cron horario de devolución de
   llamadas). El sufijo `::modal_app` es obligatorio en ambos, y la imagen ya
   empaqueta `sofia.config.yaml` y `prompts/` (sin eso, el análisis post-llamada
   falla en silencio detrás del 200 del webhook). El script **captura la
   `MODAL_URL` de la salida de Modal** y la escribe al `.env`: nunca se la pides
   al usuario, no existe antes de este paso. Sin el worker, el outbound
   simplemente nunca ocurre — sin error, sin aviso.

7. **`vercel`** — despliega el panel del cliente. Enlaza el proyecto, sube 4
   variables a producción (`BACKEND_URL`, `DASHBOARD_API_TOKEN`,
   `DASHBOARD_PASSWORD`, `DASHBOARD_SESSION_SECRET` — genera las que falten) y
   hace `vercel --prod`. El `DASHBOARD_API_TOKEN` es el secreto compartido
   panel↔backend: se genera solo, se escribe al `.env` y viaja al Modal Secret;
   si no coinciden los dos lados, `/dashboard` responde 401 y el panel carga
   vacío. Al terminar imprime la URL del panel y la contraseña generada;
   entrégalas al cliente por un canal seguro.

**Ruta recomendada** — después del `preflight` y la entrevista, corre todo de un
jalón con `python scripts/setup.py all --force` (con el Python del `.venv`).
`all` ejecuta en el orden que sobrevive los gotchas: `token del panel → validate
→ secret → deploy (backend + worker) → provision → twilio → secret (refresh con
los agent ids) → vercel`. El `preflight` y la entrevista quedan aparte porque
son interactivos.

## Cómo te comportas

- Explica en una línea qué vas a hacer antes de cada subcomando, córrelo, y lee
  su salida.
- Cuando un paso pase, resume en una frase y pasa al siguiente.
- Cuando un paso falle, **para**, muestra el error y la solución tal cual salió, y
  ayuda al usuario a corregir antes de reintentar. No maquilles un fallo como
  éxito.
- Al final, confirma: número conectado, backend desplegado, panel arriba, y
  entrégale al usuario la URL del panel y la contraseña.
