---
description: "Prueba los 5 servicios de Sofía (Retell, Twilio, GHL, Backend, Anthropic) y explica cada falla con su solución en español."
---

# /test — Sweep de servicios

Verifica que las cinco piezas de las que depende Sofía estén vivas y bien
configuradas, en este orden: **Retell → Twilio → GHL → Backend (Modal) →
Anthropic**. Cada servicio se prueba con su propio `test_connection()` real, así
que un `✓` significa que de verdad respondió, no que "debería" responder.

## Qué hacer

1. Corre el script desde la raíz del proyecto:

   ```bash
   python scripts/test_services.py
   ```

2. Lee la salida. Es un checklist con `✓` / `✗` y, debajo de cada `✗`, una línea
   `→` con la solución concreta. El script sale con código distinto de cero si
   algo falló.

## Cómo interpretarlo para el usuario

Habla en español, tuteo, directo. Por cada servicio:

- **Todo en `✓`** → díselo claro: los cinco servicios responden y Sofía puede
  operar. No hay nada más que hacer.
- **Algún `✗`** → NO le muestres el error crudo. Toma la línea `→` del script,
  que ya trae la causa y el arreglo, y explícasela con tus palabras. Por ejemplo:

  - _Retell rechazó la API key_ → "Revisa `RETELL_API_KEY` en tu `.env` y que la
    cuenta de Retell esté activa."
  - _Twilio: el número no está en la cuenta_ → "El número de `TWILIO_PHONE_NUMBER`
    no aparece en esta cuenta; verifica que lo compraste aquí y que lleva `+` y lada."
  - _GHL rechazó el token_ → "Revisa `HIGHLEVEL_PIT` y que tenga los scopes
    contacts, calendars y opportunities sobre esta Location."
  - _Backend no respondió_ → "Vuelve a desplegar con
    `modal deploy app/main.py::modal_app` y confirma que `MODAL_URL` apunta a esa URL."
  - _Anthropic rechazó la API key_ → "Revisa `ANTHROPIC_API_KEY` en tu `.env` y que
    la cuenta tenga créditos."

- **GHL con advertencia de zona horaria** → aunque salga `✓`, si aparece una
  advertencia de _timezone mismatch_, señálala: cada cita se agendaría a la hora
  equivocada en silencio. Hay que alinear `sofia.config.yaml` con la zona de GHL.

## Reglas

- Nunca imprimas ni repitas secretos (API keys, tokens).
- Nunca dejes un stacktrace crudo como respuesta final: traduce siempre a causa +
  solución.
- Si el usuario resuelve algo, vuelve a correr el script para confirmar el `✓`.
