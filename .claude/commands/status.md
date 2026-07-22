---
description: "Estado en vivo de Sofía: servicios arriba, versión publicada del agente y resumen de la última llamada, leído de Retell y GHL."
---

# /status — Estado en vivo

Foto del momento: qué servicios están arriba, qué versión del agente está
**publicada** (la que el número realmente contesta, nunca un borrador) y cómo fue
la última llamada. Es solo lectura — no escribe en ningún sistema.

## Qué hacer

1. Corre el script desde la raíz del proyecto:

   ```bash
   python scripts/status.py
   ```

2. La salida tiene cuatro bloques: **Backend**, **Servicios**, **Agente
   publicado** y **Última llamada**.

## Cómo interpretarlo para el usuario

Habla en español, tuteo, breve y concreto.

- **Backend** → el `/health` de Modal. Si dice `degraded`, le falta configuración
  (calendar_id, pipeline_id, stage_id o timezone en `sofia.config.yaml`); avísale
  y sugiere volver a desplegar.
- **Servicios** → Retell, Twilio, GHL y Anthropic con `✓` / `✗`. Si algo está
  caído, no muestres el error crudo: dile qué servicio es y mándalo a `/test`,
  que da el diagnóstico con solución.
- **Agente publicado** → la versión `V{n}` que sirve el número en vivo, su voz y
  el prompt en vivo. Esto SIEMPRE es la versión **publicada**, no el borrador: si
  alguien editó en la consola de Retell y no publicó, ese cambio NO aparece aquí
  a propósito, porque el paciente no lo está escuchando. Si el script dice "sin
  versión publicada", hay que publicar el agente una vez.
- **Última llamada** → fecha, duración y motivo de cierre vienen de Retell; el
  resultado del paciente (nombre, resumen, score, urgencia) viene de **GHL**,
  que es la fuente de la verdad. Si no hay contacto en GHL, la persona colgó sin
  calificar — es un resultado normal, no una falla.

## Reglas

- Nunca imprimas secretos.
- Deja claro que la versión del agente es la publicada (la que se oye en el
  teléfono), no un borrador guardado sin publicar.
- Si hay servicios caídos, remite a `/test` para el arreglo paso a paso.
