# INSTALLER-FIXES — bugs del `/setup` encontrados en la corrida en vivo del V11

> El `/setup` nunca se había corrido end-to-end antes de publicar el repo. Esta corrida (dry-run
> de facto) los destapó. El sistema funciona — el agente levantó a Sofía librando cada uno a mano;
> lo que hay que arreglar es la automatización del instalador. Arreglar TODOS en una pasada limpia
> desde el repo dev (`agente-voz-ghl`) + push, ANTES de re-grabar el V11 y promover el repo.

## Estado: los 7 arreglados en código

| # | Arreglo                                | Dónde quedó                                                        |
| - | -------------------------------------- | ------------------------------------------------------------------ |
| 1 | Preflight de Python 3.12               | `setup.py cmd_preflight` (nuevo subcomando, paso 0) + `INSTALAR.md` |
| 2 | Huevo-y-gallina en `validate`          | `validate.validate_retell` + `retell_service.test_api_key`          |
| 3 | Nunca preguntar la `MODAL_URL`         | fuera de `_CREDENTIALS`; la captura `cmd_deploy`                    |
| 4 | `bind_outbound_agent` sin llamar       | `twilio_service.connect_number_to_retell`                           |
| 5 | El worker no se desplegaba             | `cmd_deploy` despliega también `app/worker.py::modal_app`           |
| 6 | Agentes sin versión publicada          | `retell_service.publish_initial_version`, desde `provision_*`       |
| 7 | `DASHBOARD_API_TOKEN` sin generar      | `setup.ensure_dashboard_api_token`, al inicio de `all`              |

**Falta:** correr `/setup` end-to-end en un clon limpio (número suena + panel arriba sin librar
nada a mano) antes de re-grabar el V11.

## Los 7 arreglos

1. **Preflight de Python no es proactivo.** Hoy `/setup` se topa con Python 3.14 (Modal solo
   soporta 3.12) como bloqueador a media instalación. Fix: como PRIMER paso, detectar la versión →
   ofrecer instalar 3.12 (Homebrew) → crear el venv con 3.12. Reflejarlo en la sección "para el
   agente" de `INSTALAR.md`.

2. **`setup.py all` muere en `validate` (huevo-y-gallina).** `validate` exige
   `RETELL_INBOUND_AGENT_ID`, que aún no existe antes de `provision`. Mata el flujo antes de llegar
   a provision. Fix: correr `provision` ANTES de `validate`, o que `validate` tolere que aún no haya
   agent ids cuando los agentes no existen.

3. **Le pide al usuario la `MODAL_URL`.** La entrevista pregunta la "URL pública del backend" — que
   solo existe DESPUÉS de `modal deploy`. Fix: orden `deploy → capturar la URL del output →
   provisionar Retell con ella`. Nunca preguntarla.

4. **`bind_outbound_agent` está definida pero NUNCA se llama.** El paso `twilio` ata el inbound al
   número pero jamás el outbound → el número queda atado a un agente outbound viejo/colgado y el
   verificador de Twilio (que sí exige el outbound) truena. Fix: `connect_number_to_retell` debe
   llamar `bind_outbound_agent()` después de importar el número.

5. **`/setup` nunca despliega el worker.** Sin un `modal deploy app/worker.py::modal_app` manual, el
   cron de outbound (devolución de llamadas) no existe, aunque Retell esté aprobado. Fix: `/setup`
   despliega también el worker. (Recordar el sufijo `::modal_app`.)

6. **`provision` nunca publica los agentes.** Los agentes quedan sin versión publicada → el panel de
   control arranca roto con `source_unavailable: Agent ... has no published version to base a change
   on`. Fix: `provision` publica la v0 de cada agente (inbound + outbound) tras crearlos. Esto es
   justo la base que necesita `publish_agent_change` del panel.

7. **`cmd_vercel` no genera ni propaga `DASHBOARD_API_TOKEN`.** Genera la contraseña y el session
   secret, pero no el token compartido panel↔backend, que debe existir en `.env` Y viajar al Modal
   Secret. Sin él, los endpoints `/dashboard` rechazan al panel. Fix: `cmd_vercel` genera
   `DASHBOARD_API_TOKEN`, lo escribe al `.env` y lo mete al Modal Secret.

## Gate externo (no es bug)

- **Verificación de identidad de Retell** para llamadas salientes. El inbound jala sin eso; el
  outbound no. `/setup` debe avisarlo claro, no fallar en silencio.

## Después de arreglar

- Re-verificar corriendo `/setup` (o el "instálalo") end-to-end en un clon limpio → número suena +
  panel arriba SIN librar nada a mano.
- Recién ahí re-grabar el V11 sobre un `/setup` sólido.
