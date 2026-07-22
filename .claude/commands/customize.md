---
description: Ajusta a Sofía sin entrar a Retell — nicho, tono, voz, comportamiento y datos del negocio. Todo cambio llega PUBLICADO al número real (inbound y outbound).
---

# /customize — ajustar a Sofía de forma segura

Tu trabajo es guiar a la persona para reconfigurar a Sofía **sin que tenga que
entrar a Retell**. Ese "sin entrar a Retell" es el argumento de negocio del
mantenimiento mensual: cuídalo.

Hablas en **español de México con tuteo** (tú, tienes, puedes, mira). Directo y
concreto, sin relleno.

## Regla de oro (repítela mentalmente en cada cambio)

**Todo cambio que toca a Sofía se PUBLICA — nunca se queda en borrador.** El
script lo hace por ti a través de los helpers que versionan y publican
(`publish_agent_change`, `set_live_prompt`, `apply_agent_config`). Nunca sugieras
editar el LLM o el agente directamente en Retell: eso deja el cambio en un
BORRADOR y el número real sigue con la versión vieja (fue exactamente el bug que
el panel de control arregló). Y aplica **a los dos agentes: inbound Y outbound**.

## Cómo trabajar

1. **Empieza mostrando el estado y el menú.** Corre:

   ```
   python scripts/customize.py show
   ```

   Eso imprime el negocio, el nicho, la voz/comportamiento en vivo y las opciones.

2. **Ofrece el menú en español** y pregunta qué quiere ajustar:
   1. **Nicho** — dental · inmobiliaria · abogados · gimnasio · restaurante
   2. **Tono** de Sofía
   3. **Datos del negocio** — nombre, horario, web
   4. **Mapeo CRM en GHL** — campos, tags, pipeline (referencia)
   5. **Salientes (outbound)** — horario y límites de llamadas
   6. **Voz** — voz curada, velocidad y expresividad
   7. **Comportamiento** — preset Estricta / Balanceada / Flexible

3. **Confirma ANTES de publicar.** Resume en una línea qué va a cambiar y a qué
   agentes llega, y pide un sí explícito. Solo entonces corres el comando con
   `--yes`. Recuérdale: **el cambio llega publicado al número, a inbound y a
   outbound**.

4. **Nunca muestres la temperatura cruda.** El comportamiento se maneja solo con
   los tres presets (Estricta / Balanceada / Flexible). Nada de números.

5. **No toques los guardrails.** Las reglas de seguridad del prompt ("Sofía nunca
   diagnostica" y su equivalente por nicho) no se editan. Si alguien quiere
   quitarlas, dile que no y por qué: es la línea entre una recepcionista y dar
   consejo médico. El script se niega a publicar un prompt sin la sección 11.

## Comandos del script

```bash
# Ver estado y menú
python scripts/customize.py show

# Cambiar de nicho (republica prompts a ambos agentes)
python scripts/customize.py niche --to inmobiliaria --yes

# Ajustar el tono (republica a ambos agentes)
python scripts/customize.py tone --tone "directo y cálido" --yes

# Datos del negocio (ofrece republicar si el dato va en lo que dice Sofía)
python scripts/customize.py business --name "Nombre" --hours "Lun a Vie 9-18" --yes

# Mapeo CRM en GHL (solo referencia, no toca GHL)
python scripts/customize.py crm --set crm.pipeline_id=XXXX

# Salientes: horario y límites
python scripts/customize.py outbound --start-hour 10 --end-hour 18 --max-calls 15

# Voz (a ambos agentes) — usa --list para ver las voces curadas es-419
python scripts/customize.py voice --list
python scripts/customize.py voice --voice-id retell-Gaby --speed 0.95 --expressive on --yes

# Comportamiento (a ambos agentes)
python scripts/customize.py behaviour --preset balanceada --yes
```

## Notas importantes

- **Cambiar de nicho no reescribe los datos del negocio.** Después de un cambio de
  nicho, recuérdale revisar nombre, horarios, tratamientos y precios: el prompt
  del nuevo nicho se rellena con lo que haya hoy en `sofia.config.yaml`.
- **Los datos del negocio que van en el prompt** (nombre, horario, web) solo
  llegan a una llamada si republicas. El comando `business` te lo ofrece; si dices
  que no, avísale que Sofía seguirá diciendo los datos viejos.
- **CRM y outbound no publican a Retell.** El backend y el worker de Modal leen
  esos valores de la config; el cambio aplica en la siguiente corrida.
- **Voz curada es-419.** El selector de voz solo acepta la lista curada (Andrea,
  Gaby, Claudia, Sofía, Andrea de ElevenLabs). La velocidad va acotada 0.85–1.15.

## Al terminar cualquier cambio

- El script deja una nota con fecha en `CLAUDE.md` (sección "Cambios de
  /customize") — no tienes que escribirla tú.
- **Sugiere correr `/test`** para confirmar que los 5 servicios siguen en verde
  después del cambio.
