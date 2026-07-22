#!/usr/bin/env python3
"""`/customize` — safe niche / voice / behaviour changes that reach the LIVE number.

WHAT THIS TOOL IS FOR
    Let the agency (or a technical operator) reconfigure Sofía without opening the
    Retell console: switch the business niche, edit the tone, tune the voice and
    speed, pick a behaviour preset, and adjust business / CRM / outbound data in
    `sofia.config.yaml`. Every option is idempotent — running it twice with the
    same value changes nothing the second time.

THE GOLDEN RULE (repeated here and next to every publish call)
    EVERY change that must reach Retell goes through the PUBLISHING helpers in
    app/services/retell_service.py:

        - publish_agent_change()  -> versioned create_version -> update -> publish
        - set_live_prompt()       -> routes through publish_agent_change (inbound)
        - apply_agent_config()    -> publish_agent_change on BOTH managed agents

    NEVER call llm.update / agent.update directly: those write to a DRAFT and the
    real phone number keeps serving the last PUBLISHED version. That silent-draft
    bug is exactly what the control panel was built to kill. This applies to the
    INBOUND agent AND the OUTBOUND agent.

CONFIG vs RETELL
    - Business / CRM / outbound data lives in `sofia.config.yaml` (versioned, no
      secrets). We edit it in place, preserving its comments.
    - Anything baked into the spoken prompt (name, hours, website, tone, niche)
      only reaches a live call once the prompt is re-rendered and PUBLISHED to
      both agents. Those commands offer to republish.

This module does not print or accept any secret. Credentials live in `.env` and
are read by the service layer, never here.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

# Make `app` importable when this script is run directly.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# ghl_service only needs pyyaml/requests — safe to import even without the Retell
# SDK present, so config-only commands keep working. The Retell service is
# imported LAZILY inside the publishing helpers.
from app.services import ghl_service  # noqa: E402
from app.services.ghl_service import config_value  # noqa: E402

CONFIG_PATH: Path = ghl_service._CONFIG_PATH
CLAUDE_MD: Path = _REPO_ROOT / "CLAUDE.md"

VALID_NICHES = ("dental", "inmobiliaria", "abogados", "gimnasio", "restaurante")

# The prompt's SAFETY & SCOPE GUARDRAILS block is section 11 of the 12-component
# structure. We refuse to publish a prompt that lost it — that is the "nunca
# diagnostica" line (and its per-niche equivalent) the client must not delete.
_SAFETY_SECTION_RE = re.compile(r"^[ \t]*#[ \t]*11\.", re.MULTILINE)

# Human labels for the messages we print to the operator (Spanish, tuteo MX).
_NICHE_LABEL = {
    "dental": "Clínica dental",
    "inmobiliaria": "Inmobiliaria",
    "abogados": "Despacho de abogados",
    "gimnasio": "Gimnasio",
    "restaurante": "Restaurante",
}


# ==========================================================================
# Output helpers — every user-facing message is Spanish (tuteo de México).
# ==========================================================================


def _say(msg: str) -> None:
    print(msg)


def _warn(msg: str) -> None:
    print(f"⚠️  {msg}")


def _ok(msg: str) -> None:
    print(f"✅ {msg}")


def _err(msg: str) -> None:
    print(f"❌ {msg}", file=sys.stderr)


def _confirm(question: str, assume_yes: bool) -> bool:
    """Ask before publishing. `--yes` skips it; a non-interactive shell must pass --yes."""
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        _warn(
            "Este cambio publica en el número real y necesita confirmación. "
            "Vuelve a correrlo con --yes cuando estés seguro."
        )
        return False
    answer = input(f"{question} [s/N]: ").strip().lower()
    return answer in ("s", "si", "sí", "y", "yes")


def _suggest_test() -> None:
    _say("")
    _say("👉 Corre `/test` para confirmar que todo sigue en verde después del cambio.")


# ==========================================================================
# CLAUDE.md changelog — a one-line note per change.
# ==========================================================================

_CHANGELOG_HEADER = "## Cambios de /customize"


def _note_change(summary: str) -> None:
    """Append `- YYYY-MM-DD: <summary>` under the changelog section in CLAUDE.md."""
    date = datetime.now().strftime("%Y-%m-%d")
    line = f"- {date}: {summary}\n"
    try:
        text = CLAUDE_MD.read_text(encoding="utf-8") if CLAUDE_MD.exists() else ""
    except OSError as exc:
        _warn(f"No pude leer CLAUDE.md para dejar la nota: {exc}")
        return

    if _CHANGELOG_HEADER not in text:
        if text and not text.endswith("\n"):
            text += "\n"
        text += f"\n{_CHANGELOG_HEADER}\n\n"
    text = text.rstrip("\n") + "\n" + line

    try:
        CLAUDE_MD.write_text(text, encoding="utf-8")
    except OSError as exc:
        _warn(f"No pude escribir la nota en CLAUDE.md: {exc}")


# ==========================================================================
# Comment-preserving YAML scalar editing.
#
# `sofia.config.yaml` is heavily commented and those comments are load-bearing
# documentation. A full YAML round-trip (pyyaml) would drop every comment, so we
# edit the target line SURGICALLY: find the leaf by its indentation-nested path
# and rewrite only its value, keeping any inline comment intact.
# ==========================================================================

_KEY_RE = re.compile(r"^(?P<indent>[ \t]*)(?P<key>[A-Za-z0-9_]+):(?P<rest>.*)$")


def _split_value_comment(rest: str) -> tuple[str, str]:
    """Split ` value   # comment` into (value_stripped, comment_with_original_gap).

    The comment keeps the exact whitespace that preceded its `#`, so rewriting an
    unchanged value reproduces the line byte for byte and the comment alignment is
    left alone. A `#` only opens a comment when it is preceded by whitespace and is
    not inside quotes; our edited values are simple (names, urls, numbers, hours)
    and never contain `#`, so this is safe.
    """
    in_quote = False
    for i, ch in enumerate(rest):
        if ch == '"':
            in_quote = not in_quote
        elif ch == "#" and not in_quote and (i == 0 or rest[i - 1] in " \t"):
            gap_start = i
            while gap_start > 0 and rest[gap_start - 1] in " \t":
                gap_start -= 1
            return rest[:gap_start].strip(), rest[gap_start:]
    return rest.strip(), ""


def _atom(value, orig_value: str = "") -> str:
    """Format one scalar, preserving the original quoting style when sensible."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    text = str(value)
    orig = (orig_value or "").strip()
    needs_quotes = orig.startswith('"') or (text and (text[0] in "#&*!|>%@`" or ": " in text))
    if needs_quotes:
        return '"' + text.replace('"', '\\"') + '"'
    return text


def _format_scalar(value, orig_value: str = "") -> str:
    if isinstance(value, list):
        return "[" + ", ".join(_atom(item) for item in value) + "]"
    return _atom(value, orig_value)


def _set_yaml_scalar(text: str, dotted_path: str, new_value) -> tuple[str, bool]:
    """Rewrite the scalar at `dotted_path`. Returns (new_text, changed).

    Idempotent: if the leaf already holds the requested value, nothing is written
    and `changed` is False.
    """
    target = dotted_path.split(".")
    lines = text.splitlines(keepends=True)
    stack: list[tuple[int, str]] = []  # (indent, key) nesting stack

    for index, raw in enumerate(lines):
        newline = "\n" if raw.endswith("\n") else ""
        body = raw[:-1] if newline else raw
        match = _KEY_RE.match(body)
        if not match:
            continue
        indent = len(match.group("indent"))
        key = match.group("key")
        while stack and stack[-1][0] >= indent:
            stack.pop()
        stack.append((indent, key))
        if [k for _, k in stack] != target:
            continue

        orig_value, comment = _split_value_comment(match.group("rest"))
        formatted = _format_scalar(new_value, orig_value)
        # Idempotent: the value already holds this, so leave the line (and its
        # comment alignment) exactly as it is.
        if formatted == orig_value:
            return text, False
        rebuilt = f"{match.group('indent')}{key}: {formatted}{comment}".rstrip()
        lines[index] = rebuilt + newline
        return "".join(lines), True

    raise KeyError(f"No encontré `{dotted_path}` en {CONFIG_PATH.name}")


def _apply_config_edits(edits: list[tuple[str, object]], *, label: str) -> bool:
    """Apply a batch of (dotted_path, value) edits to the config file. Idempotent."""
    text = CONFIG_PATH.read_text(encoding="utf-8")
    changed_any = False
    for dotted_path, value in edits:
        try:
            text, changed = _set_yaml_scalar(text, dotted_path, value)
        except KeyError as exc:
            _err(str(exc))
            return False
        if changed:
            changed_any = True
            _ok(f"{dotted_path} = {value}")
        else:
            _say(f"• {dotted_path} ya estaba en ese valor, lo dejo igual.")

    if not changed_any:
        _say("No hubo cambios que aplicar.")
        return False

    CONFIG_PATH.write_text(text, encoding="utf-8")
    _note_change(f"{label} en sofia.config.yaml")
    return True


# ==========================================================================
# Retell publishing — the ONLY path to the live number.
#
# GOLDEN RULE: everything below reaches Retell through publish_agent_change /
# set_live_prompt / apply_agent_config, which version + PUBLISH. No direct
# llm.update / agent.update anywhere. Applies to inbound AND outbound.
# ==========================================================================


def _assert_guardrails(prompt: str, which: str) -> None:
    """Refuse to publish a prompt that lost its SAFETY & SCOPE GUARDRAILS (section 11)."""
    if not _SAFETY_SECTION_RE.search(prompt):
        raise SystemExit(
            f"❌ El prompt de {which} no tiene la sección 11 (reglas de seguridad: "
            "'nunca diagnostica' / su equivalente por nicho). No lo voy a publicar: "
            "esas reglas son la línea entre una recepcionista y dar consejo médico. "
            "Restaura el bloque y vuelve a intentar."
        )


def _publish_prompts_for(industry: str) -> None:
    """Render inbound + outbound prompts for `industry` and PUBLISH to both agents.

    Inbound gets its inbound_prompt; outbound gets its own outbound_prompt. Both
    go out through the publishing helpers — never a raw draft update.
    """
    # Lazy import: the Retell SDK is only needed when we actually touch Retell.
    from app.services import prompt_history, retell_service

    inbound_prompt = retell_service.load_prompt("inbound_prompt", industry=industry)
    outbound_prompt = retell_service.load_prompt("outbound_prompt", industry=industry)
    _assert_guardrails(inbound_prompt, "inbound")
    _assert_guardrails(outbound_prompt, "outbound")

    # Save an undo baseline first, exactly like the panel does: if this publish is
    # the one that breaks Sofía, the one-click restore must already exist.
    try:
        previous = retell_service.get_live_prompt()
        durable = prompt_history.save_previous(previous, saved_at=datetime.now().isoformat())
        if not durable:
            _warn("El respaldo del prompt anterior es temporal (Modal no disponible).")
    except Exception as exc:  # noqa: BLE001 - undo is best-effort, never blocks the change
        _warn(f"No pude guardar el respaldo del prompt anterior: {exc}")

    # --- Inbound: routes through publish_agent_change (versioned + PUBLISH). ---
    result = retell_service.set_live_prompt(inbound_prompt)
    _ok(f"Inbound publicado (v{result.get('published_version')}). El número ya usa el nuevo prompt.")

    # --- Outbound: its own prompt, PUBLISHED via publish_agent_change. ---
    outbound_id = retell_service._outbound_agent_id()
    if outbound_id:
        out = retell_service.publish_agent_change(outbound_id, llm_general_prompt=outbound_prompt)
        _ok(f"Outbound publicado (v{out.get('published_version')}).")
    else:
        _warn("No hay agente outbound configurado (RETELL_OUTBOUND_AGENT_ID vacío). Solo publiqué inbound.")


# ==========================================================================
# Commands
# ==========================================================================


def cmd_show(_: argparse.Namespace) -> int:
    """Read-only: current business data + live voice/behaviour + the menu."""
    _say("── Estado actual ──")
    try:
        _say(f"Negocio:   {config_value('business.name')}")
        _say(f"Nicho:     {config_value('business.industry')}")
        _say(f"Horario:   {config_value('business.hours')}")
        _say(f"Web:       {config_value('business.website')}")
        _say(f"Outbound:  {'activo' if config_value('outbound.enabled') else 'inactivo'}")
    except Exception as exc:  # noqa: BLE001
        _warn(f"No pude leer sofia.config.yaml: {exc}")

    # Voice + behaviour come from the LIVE (published) agent, so this is the truth
    # the caller hears — never a raw temperature, only the preset name.
    try:
        from app.services import retell_service

        cfg = retell_service.current_agent_config()
        voice_label = next(
            (v["label"] for v in cfg.get("curated_voices", []) if v["voice_id"] == cfg.get("voice_id")),
            cfg.get("voice_id"),
        )
        _say("")
        _say(f"Voz:            {voice_label}")
        _say(f"Velocidad:      {cfg.get('voice_speed')}")
        _say(f"Expresividad:   {'activada' if cfg.get('expressiveness') else 'moderada'}")
        _say(f"Comportamiento: {cfg.get('behaviour')}")
        _say(f"Agentes en sync: {', '.join(cfg.get('synced_agents', []))}")
    except Exception as exc:  # noqa: BLE001
        _warn(f"No pude leer la config en vivo de Retell: {exc}")

    _say("")
    _say("── Qué puedes ajustar ──")
    _say("  1. niche      — cambiar de nicho (dental/inmobiliaria/abogados/gimnasio/restaurante)")
    _say("  2. tone       — ajustar el tono de Sofía")
    _say("  3. business   — nombre, horario, web del negocio")
    _say("  4. crm        — mapeo de campos/tags/pipeline en GHL (referencia)")
    _say("  5. outbound   — horario y límites de las llamadas salientes")
    _say("  6. voice      — voz, velocidad y expresividad")
    _say("  7. behaviour  — preset Estricta / Balanceada / Flexible")
    _say("")
    _say("Todo cambio que toca a Sofía se PUBLICA a ambos agentes (inbound y outbound).")
    return 0


def cmd_niche(args: argparse.Namespace) -> int:
    """Switch niche: repoint business.industry and publish the new niche prompts."""
    target = args.to
    if target not in VALID_NICHES:
        _err(f"Nicho no válido. Opciones: {', '.join(VALID_NICHES)}")
        return 1

    current = config_value("business.industry")
    if current == target:
        _say(f"El nicho ya es «{target}». Republico los prompts por si hubo ediciones.")
    else:
        _say(f"Vas a cambiar el nicho de «{current}» a «{_NICHE_LABEL[target]}».")
        _warn(
            "Cambiar de nicho NO reescribe los datos del negocio. Después de esto revisa "
            "nombre, horarios, tratamientos y precios con `business` — el prompt del nuevo "
            "nicho se rellena con lo que haya hoy en sofia.config.yaml."
        )

    if not _confirm("¿Aplico el cambio de nicho y publico a ambos agentes?", args.yes):
        _say("Cancelado. No toqué nada.")
        return 0

    text = CONFIG_PATH.read_text(encoding="utf-8")
    text, _changed = _set_yaml_scalar(text, "business.industry", target)
    CONFIG_PATH.write_text(text, encoding="utf-8")

    try:
        _publish_prompts_for(target)
    except SystemExit as exc:
        _err(str(exc))
        return 1
    except Exception as exc:  # noqa: BLE001
        _err(f"Falló la publicación en Retell: {exc}")
        return 1

    _note_change(f"nicho -> {target}, prompts publicados a inbound y outbound")
    _suggest_test()
    return 0


def cmd_tone(args: argparse.Namespace) -> int:
    """Edit agent.tone and republish the current niche prompts (tone is baked in)."""
    new_tone = args.tone.strip()
    if not new_tone:
        _err("El tono no puede ir vacío.")
        return 1

    _say(f"Nuevo tono de Sofía: «{new_tone}»")
    if not _confirm("¿Guardo el tono y republico a ambos agentes?", args.yes):
        _say("Cancelado. No toqué nada.")
        return 0

    text = CONFIG_PATH.read_text(encoding="utf-8")
    text, changed = _set_yaml_scalar(text, "agent.tone", new_tone)
    if not changed:
        _say("El tono ya estaba así. No republico.")
        return 0
    CONFIG_PATH.write_text(text, encoding="utf-8")

    industry = config_value("business.industry")
    try:
        _publish_prompts_for(industry)
    except SystemExit as exc:
        _err(str(exc))
        return 1
    except Exception as exc:  # noqa: BLE001
        _err(f"Falló la publicación en Retell: {exc}")
        return 1

    _note_change(f"tono de Sofía -> «{new_tone}», prompts republicados")
    _suggest_test()
    return 0


def cmd_business(args: argparse.Namespace) -> int:
    """Edit business data. Fields baked into the prompt trigger an offer to republish."""
    edits: list[tuple[str, object]] = []
    if args.name is not None:
        edits.append(("business.name", args.name))
    if args.website is not None:
        edits.append(("business.website", args.website))
    if args.hours is not None:
        edits.append(("business.hours", args.hours))
    if args.hours_start is not None:
        edits.append(("business.hours_start", args.hours_start))
    if args.hours_end is not None:
        edits.append(("business.hours_end", args.hours_end))
    for pair in args.set or []:
        edits.append(_parse_set(pair))

    if not edits:
        _err("No indicaste nada que cambiar. Usa --name, --hours, --website, o --set clave=valor.")
        return 1

    if not _apply_config_edits(edits, label="datos del negocio"):
        return 0

    # name / hours / website live inside the spoken prompt. The config edit alone
    # does not reach a live call — only a republish does.
    baked = {"business.name", "business.website", "business.hours"}
    touches_prompt = any(path in baked for path, _ in edits)
    if touches_prompt and not args.no_publish:
        if _confirm("Estos datos van en lo que dice Sofía. ¿Republico a ambos agentes?", args.yes):
            try:
                _publish_prompts_for(config_value("business.industry"))
            except SystemExit as exc:
                _err(str(exc))
                return 1
            except Exception as exc:  # noqa: BLE001
                _err(f"Falló la publicación en Retell: {exc}")
                return 1
        else:
            _warn("Guardé los datos, pero Sofía seguirá diciendo los anteriores hasta que republiques.")

    _suggest_test()
    return 0


def cmd_crm(args: argparse.Namespace) -> int:
    """Adjust the GHL field/tag/pipeline mapping in the config (reference only, no GHL calls)."""
    edits = [_parse_set(pair) for pair in (args.set or [])]
    if not edits:
        _err("Usa --set con rutas bajo crm, por ejemplo: --set crm.pipeline_id=XXXX")
        return 1
    # These are references the backend reads; editing them here does not touch GHL.
    _apply_config_edits(edits, label="mapeo CRM (GHL)")
    _say("Nota: esto solo cambia el mapeo que lee el backend. No modifica nada dentro de GHL.")
    return 0


def cmd_outbound(args: argparse.Namespace) -> int:
    """Edit the outbound worker schedule/limits in the config. The worker reads it — no Retell publish."""
    edits: list[tuple[str, object]] = []
    if args.enabled is not None:
        edits.append(("outbound.enabled", args.enabled))
    if args.cron is not None:
        edits.append(("outbound.schedule_cron", args.cron))
    if args.start_hour is not None:
        edits.append(("outbound.call_window.start_hour", args.start_hour))
    if args.end_hour is not None:
        edits.append(("outbound.call_window.end_hour", args.end_hour))
    if args.weekdays is not None:
        edits.append(("outbound.call_window.weekdays", args.weekdays))
    if args.max_calls is not None:
        edits.append(("outbound.max_calls_per_run", args.max_calls))
    if args.cooldown is not None:
        edits.append(("outbound.cooldown_hours", args.cooldown))
    if args.max_attempts is not None:
        edits.append(("outbound.max_attempts", args.max_attempts))
    for pair in args.set or []:
        edits.append(_parse_set(pair))

    if not edits:
        _err("No indicaste nada. Usa --start-hour, --end-hour, --cron, --max-calls, etc.")
        return 1

    _apply_config_edits(edits, label="horario/límites de outbound")
    _say("El worker de Modal lee esto en su próxima corrida (cron horario). No hay que publicar a Retell.")
    return 0


def cmd_voice(args: argparse.Namespace) -> int:
    """Pick a curated es-419 voice + speed + expressiveness, PUBLISHED to both agents."""
    from app.services import retell_service

    if args.list:
        _say("Voces disponibles (es-419, curadas):")
        for voice in retell_service.CURATED_VOICES:
            _say(f"  {voice['voice_id']:<18} {voice['label']:<20} {voice['note']}")
        _say(f"\nVelocidad permitida: {retell_service.VOICE_SPEED_MIN}–{retell_service.VOICE_SPEED_MAX}")
        return 0

    kwargs: dict[str, object] = {}
    if args.voice_id is not None:
        kwargs["voice_id"] = args.voice_id
    if args.speed is not None:
        kwargs["voice_speed"] = args.speed
    if args.expressive is not None:
        kwargs["expressiveness"] = args.expressive == "on"

    if not kwargs:
        _err("No indicaste nada. Usa --voice-id, --speed o --expressive. (`--list` para ver las voces.)")
        return 1

    if not _confirm("¿Aplico la voz y publico a ambos agentes?", args.yes):
        _say("Cancelado. No toqué nada.")
        return 0

    try:
        # apply_agent_config validates bounds and PUBLISHES to every managed agent.
        result = retell_service.apply_agent_config(**kwargs)  # type: ignore[arg-type]
    except ValueError as exc:
        _err(f"Valor fuera de rango: {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001
        _err(f"Falló la publicación en Retell: {exc}")
        return 1

    agents = ", ".join(item["agent"] for item in result.get("applied", []))
    _ok(f"Voz publicada a: {agents}.")
    _note_change(f"voz/velocidad/expresividad actualizada ({', '.join(kwargs)})")
    _suggest_test()
    return 0


def cmd_behaviour(args: argparse.Namespace) -> int:
    """Set the behaviour preset (Estricta/Balanceada/Flexible), PUBLISHED to both agents.

    The client never sees the raw temperature — only the three preset names.
    """
    from app.services import retell_service

    preset = args.preset.lower()
    presets = list(retell_service.BEHAVIOUR_PRESETS)
    if preset not in presets:
        _err(f"Preset no válido. Opciones: {', '.join(presets)}")
        return 1

    _say(f"Comportamiento de Sofía -> «{preset}».")
    if not _confirm("¿Aplico y publico a ambos agentes?", args.yes):
        _say("Cancelado. No toqué nada.")
        return 0

    try:
        # behaviour maps to a bounded temperature INSIDE apply_agent_config; we
        # never handle or print the number here.
        result = retell_service.apply_agent_config(behaviour=preset)
    except ValueError as exc:
        _err(str(exc))
        return 1
    except Exception as exc:  # noqa: BLE001
        _err(f"Falló la publicación en Retell: {exc}")
        return 1

    agents = ", ".join(item["agent"] for item in result.get("applied", []))
    _ok(f"Comportamiento «{preset}» publicado a: {agents}.")
    _note_change(f"comportamiento -> {preset}")
    _suggest_test()
    return 0


# ==========================================================================
# Argument parsing
# ==========================================================================


def _parse_set(pair: str) -> tuple[str, object]:
    """Parse `dotted.path=value`, coercing ints / true / false where obvious."""
    if "=" not in pair:
        raise SystemExit(f"❌ `{pair}` no tiene forma clave=valor.")
    path, raw = pair.split("=", 1)
    path, raw = path.strip(), raw.strip()
    value: object = raw
    if raw.lower() in ("true", "false"):
        value = raw.lower() == "true"
    elif re.fullmatch(r"-?\d+", raw):
        value = int(raw)
    return path, value


def _bool_flag(value: str) -> bool:
    return value.lower() in ("true", "on", "1", "si", "sí", "yes")


def _weekdays(value: str) -> list[int]:
    return [int(x) for x in re.split(r"[,\s]+", value.strip()) if x != ""]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="customize",
        description="Cambios seguros de nicho / voz / comportamiento que llegan PUBLICADOS al número.",
    )
    parser.add_argument("--yes", action="store_true", help="No pedir confirmación (para automatizaciones).")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("show", help="Ver el estado actual y el menú de opciones.")

    p_niche = sub.add_parser("niche", help="Cambiar de nicho y publicar los prompts.")
    p_niche.add_argument("--to", required=True, choices=VALID_NICHES, help="Nicho destino.")

    p_tone = sub.add_parser("tone", help="Ajustar el tono de Sofía y republicar.")
    p_tone.add_argument("--tone", required=True, help="Tono, por ejemplo: 'amable, empático, tranquilizador'.")

    p_biz = sub.add_parser("business", help="Editar datos del negocio.")
    p_biz.add_argument("--name")
    p_biz.add_argument("--website")
    p_biz.add_argument("--hours")
    p_biz.add_argument("--hours-start", dest="hours_start")
    p_biz.add_argument("--hours-end", dest="hours_end")
    p_biz.add_argument("--set", action="append", metavar="ruta=valor", help="Editar otra ruta escalar del YAML.")
    p_biz.add_argument("--no-publish", action="store_true", help="Solo guardar; no republicar el prompt.")

    p_crm = sub.add_parser("crm", help="Ajustar el mapeo de campos/tags/pipeline de GHL (referencia).")
    p_crm.add_argument("--set", action="append", metavar="ruta=valor", required=False, help="Ej: crm.pipeline_id=XXXX")

    p_out = sub.add_parser("outbound", help="Horario y límites de las llamadas salientes.")
    p_out.add_argument("--enabled", type=_bool_flag, metavar="on|off")
    p_out.add_argument("--cron", help="Expresión cron, por ejemplo '0 * * * *'.")
    p_out.add_argument("--start-hour", dest="start_hour", type=int, metavar="0-23")
    p_out.add_argument("--end-hour", dest="end_hour", type=int, metavar="0-23")
    p_out.add_argument("--weekdays", type=_weekdays, metavar='"0,1,2,3,4"', help="Lun=0 … Dom=6.")
    p_out.add_argument("--max-calls", dest="max_calls", type=int)
    p_out.add_argument("--cooldown", type=int, metavar="horas")
    p_out.add_argument("--max-attempts", dest="max_attempts", type=int)
    p_out.add_argument("--set", action="append", metavar="ruta=valor")

    p_voice = sub.add_parser("voice", help="Voz, velocidad y expresividad (a ambos agentes).")
    p_voice.add_argument("--list", action="store_true", help="Ver las voces curadas es-419.")
    p_voice.add_argument("--voice-id", dest="voice_id")
    p_voice.add_argument("--speed", type=float, metavar="0.85-1.15")
    p_voice.add_argument("--expressive", choices=("on", "off"))

    p_beh = sub.add_parser("behaviour", help="Preset de comportamiento (a ambos agentes).")
    p_beh.add_argument("--preset", required=True, choices=("estricta", "balanceada", "flexible"))

    return parser


_DISPATCH = {
    "show": cmd_show,
    "niche": cmd_niche,
    "tone": cmd_tone,
    "business": cmd_business,
    "crm": cmd_crm,
    "outbound": cmd_outbound,
    "voice": cmd_voice,
    "behaviour": cmd_behaviour,
}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        return cmd_show(args)
    return _DISPATCH[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
