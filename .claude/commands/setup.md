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

## Pasos manuales del humano (no los automatices)

Dos cosas las hace el usuario una sola vez, tú solo lo guías:

- `modal token new` — autentica el CLI de Modal. No hay `MODAL_TOKEN` en `.env`.
- `vercel login` — autentica el CLI de Vercel antes del paso del panel.

Pídeselos ANTES de llegar a los pasos de `deploy` y `vercel`. Si el CLI no está
autenticado, el subcomando falla con un mensaje claro y te detienes.

## Los subcomandos

Corre estos con el intérprete de Python del proyecto, p. ej.
`python scripts/setup.py <subcomando>`. Esta lista los describe uno por uno,
pero **no es el orden de ejecución**: las piezas dependen entre sí (el `deploy`
necesita el Secret; el `provision` necesita la `MODAL_URL` que imprime el
`deploy`). Por eso la ruta recomendada es correr `all`, que los ordena solo
(ver abajo).

1. **`interview`** — la entrevista. El usuario pega credenciales en la terminal;
   el script las guarda en `.env`. También ofrece actualizar los datos del
   negocio y los ids de GHL en `sofia.config.yaml` (viene precargado con la
   clínica ancla; Enter deja el valor actual). Acepta `--skip-interview` si el
   `.env` y el YAML ya están llenos. Este paso es interactivo: deja que el
   usuario escriba, no lo hagas por él.

2. **`validate`** — valida cada credencial contra su API real: GHL (Location +
   pipeline/stage), Retell, Twilio, Anthropic. Si algo falla, el script imprime
   el problema y la solución y sale con error. **Detente aquí** hasta que todo
   pase.

3. **`provision`** — crea los agentes de Retell (inbound y outbound) y guarda sus
   ids en `.env`. El script confirma que ambos salen con `end_call`,
   `update_lead_status` y `end_call_after_silence_ms` cableados; si faltara
   alguno, truena a propósito (es el bug histórico V06/V07/V09). Necesita
   `MODAL_URL` en `.env`: las tools del agente apuntan al backend, así que va
   DESPUÉS del `deploy`. `all` ya lo ordena; solo importa si corres los
   subcomandos sueltos.

4. **`twilio`** — conecta el número de Twilio a Retell (trunk, origination, ACL
   de IPs, import y verificación). Re-ejecutable. Se ata al
   `RETELL_INBOUND_AGENT_ID` que dejó `provision`.

5. **`secret`** — crea/actualiza el Modal Secret `agente-voz-credentials` con
   todas las llaves del `.env`. Usa `--force` para reemplazar el secreto entero
   y no perder ninguna llave: `python scripts/setup.py secret --force`.

6. **`deploy`** — despliega el backend: `modal deploy app/main.py::modal_app`. El
   sufijo `::modal_app` es obligatorio, y la imagen ya empaqueta
   `sofia.config.yaml` y `prompts/` (sin eso, el análisis post-llamada falla en
   silencio detrás del 200 del webhook). El script guarda la `MODAL_URL` que
   imprime Modal.

7. **`vercel`** — despliega el panel del cliente. Enlaza el proyecto, sube 4
   variables a producción (`BACKEND_URL`, `DASHBOARD_API_TOKEN`,
   `DASHBOARD_PASSWORD`, `DASHBOARD_SESSION_SECRET` — genera las dos últimas si
   no existen) y hace `vercel --prod`. Al terminar imprime la URL del panel y la
   contraseña generada; entrégalas al cliente por un canal seguro.

**Ruta recomendada** — después de la entrevista, corre todo de un jalón con
`python scripts/setup.py all --force`. `all` ejecuta en el orden que sobrevive
los gotchas: `validate → secret → deploy → provision → twilio → secret (refresh
con los agent ids) → vercel`. La entrevista queda aparte porque es interactiva.

## Cómo te comportas

- Explica en una línea qué vas a hacer antes de cada subcomando, córrelo, y lee
  su salida.
- Cuando un paso pase, resume en una frase y pasa al siguiente.
- Cuando un paso falle, **para**, muestra el error y la solución tal cual salió, y
  ayuda al usuario a corregir antes de reintentar. No maquilles un fallo como
  éxito.
- Al final, confirma: número conectado, backend desplegado, panel arriba, y
  entrégale al usuario la URL del panel y la contraseña.
