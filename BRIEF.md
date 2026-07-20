---
proyecto: 02 — Agentes de voz (`agente-voz-ghl`)
caso: Clínica Dental "Sonrisa Perfecta" · agente "Sofía"
estado: alcance cerrado — ver BRIEF-TECNICO.md para el detalle de implementación
actualizado: 2026-07-20
---

# Proyecto 02 — Agentes de voz

> **Brief de alcance.** Qué se construye y por qué. El *cómo* técnico (endpoints,
> estructura de archivos, configuración) vive en `BRIEF-TECNICO.md`, en esta misma carpeta.

## Dos formas de llegar al sistema

- **Construirlo:** le das estos dos briefs a Claude Code y él lo arma desde cero. Es el camino
  largo, y es el que te enseña cómo funciona por dentro.
- **Instalarlo:** clonas el repo `agente-voz-ghl`, que ya viene completo, y corres `/setup`.
  En minutos tienes tu agente sonando, sin escribir una línea.

Las dos terminan en el mismo lugar. Si eliges instalar, **no necesitas crear ningún archivo**:
todo lo que describen estos documentos ya viene hecho.

## Objetivo

Un **agente de voz (phone agent)** que atiende llamadas de forma autónoma para un negocio
local: contesta 24/7, califica a quien llama, **agenda la cita** y **llena el CRM** solo.
Además **devuelve llamadas** (outbound) a los leads pendientes y a los no-shows.

Caso ancla: **Clínica Dental "Sonrisa Perfecta"**, agente **Sofía** — el mismo negocio y
personaje del curso de GoHighLevel y del agente de WhatsApp (coherencia de toda la serie).

## Stack

- **Voz:** **Retell AI** (STT + TTS + orquestación de la llamada)
- **Número:** **Twilio** (número con voz, conectado a Retell por Elastic SIP Trunk)
- **Cerebro:** **Claude** (decisiones en llamada + análisis y resumen post-llamada)
- **Backend / tools:** **Modal** + Python (FastAPI, URL pública, worker Cron para outbound)
- **CRM + calendario + pipeline:** **GoHighLevel** — una sola subcuenta (Location)
- **Dashboard:** Next.js (ligero — **lee** de GHL y del backend, no tiene base propia)
- **Desarrollo:** Claude Code (VS Code)

> **Un proveedor por capa, a propósito.** Simplicidad sobre opciones: no hay base de datos
> aparte, no hay capa de automatización extra. GHL es la fuente de la verdad.

## Qué hace Sofía (alcance funcional)

1. **Contesta 24/7** con voz natural en español de México.
2. **Califica:** motivo de la llamada, síntoma/urgencia (dolor, hinchazón, sangrado →
   prioriza), tratamiento de interés, datos de contacto. **Nunca diagnostica.**
3. **Agenda** la cita de valoración en el calendario real de GHL.
4. **Llena el CRM:** crea el contacto, abre la oportunidad en el pipeline "Nuevos Pacientes"
   y le pone tags de temperatura.
5. **Resume la llamada** al colgar (Claude lee la transcripción) y guarda nota + score en la
   ficha del contacto.
6. **Outbound:** un worker en Modal revisa GHL cada hora y llama a no-shows y leads frescos
   para recalificar y reagendar.

## Dashboard (lo que el cliente recibe)

Capa de presentación, deliberadamente ligera. **No se construye en cámara.**

- Métricas: llamadas totales, citas agendadas, tasa de éxito, duración promedio, costos.
- Llamadas recientes con transcripción y resumen.
- Temperatura de leads y funnel, leídos de GHL.
- **Editar el prompt del agente** (los 12 componentes) sin entrar a Retell.
- Disparar una llamada outbound manual.
- Branding por cliente (logo + colores).

> El "sin entrar a Retell" es el argumento de negocio: es lo que sostiene el
> **mantenimiento mensual**.

## Referencia interna (wiki)

- `[[tecnico/retell-voice-agents]]` — arquitectura ya documentada:
  - **Marco** (restaurante Spago) — pedidos por teléfono → GHL.
  - **Avery** (Local Electric, Manitoba) — agendamiento.
  - **Estructura de prompt en 12 componentes** (Role, Context, Personality, Task,
    Conversational Flow, Knowledge Base, Guardrails, Pacing…).
  - Reglas de pacing (teléfonos dígito a dígito, correos deletreados, pausa 3 s tras
    consultar disponibilidad).

## Valores de referencia

- **LLM de Retell:** Claude Haiku · temperature 0.3–0.4
- **Voz:** ElevenLabs, español es-MX, tono cálido · speak-during-execution ON
- **GHL API:** base `services.leadconnectorhq.com` · header `Version: 2021-07-28`
- **Número Twilio local MX:** ~$6.25/mes · entrante ~$0.01/min
- **Número Twilio toll-free +52 800:** ~$30/mes · entrante ~$0.216/min

## Estado

- [x] Alcance y arquitectura definidos.
- [x] Capa de integración con GHL especificada (ver `BRIEF-TECNICO.md`, sección 4).
- [x] Caso "Sonrisa Perfecta" precargado (`sofia.config.yaml` + `prompts/dental.yaml`).
- [x] Dashboard ligero armado (capa de presentación, solo lectura).
- [ ] Repo `agente-voz-ghl` publicado con la URL final confirmada.

## Notas

- **Todo el CRM, el calendario y el pipeline viven en GoHighLevel**, en una sola subcuenta.
  Es una decisión deliberada: menos piezas, menos integraciones que mantener y una sola
  fuente de la verdad.
- El foco técnico del proyecto es **Modal · Retell · modelo/voz/latencia · conexión GHL**.
  El dashboard es intencionalmente ligero.
