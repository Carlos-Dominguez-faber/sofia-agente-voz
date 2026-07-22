"""The installer glue — deterministic, idempotent, one subcommand per step.

This is the wiring that turns an empty account into a working voice agent. It
does NOT reimplement any provider logic: it calls the service layer in
`app/services/` and the Modal/Vercel CLIs, in the one order that survives the
known gotchas.

Subcommands (the interview is separate and interactive):

  preflight   — Python 3.12 + the project venv, BEFORE anything else. Modal only
                supports 3.12 here, and finding that out halfway through an
                install is the worst time to find it out.
  interview   — the human pastes credentials; this writes them to .env. It also
                offers to update the business fields and the crm ids in
                sofia.config.yaml. Secrets are read hidden and NEVER printed.
  validate    — every credential against its real API, via scripts/validate.py.
                Stops on the first failure with the exact fix.
  secret      — push the whole .env into the Modal Secret, idempotently.
  deploy      — modal deploy of BOTH apps: app/main.py::modal_app (the tools and
                webhooks) and app/worker.py::modal_app (the outbound cron). The
                ::modal_app suffix is mandatory; the image already packs
                sofia.config.yaml + prompts/. Captures the MODAL_URL it prints
                back into .env — it is never asked for in the interview, because
                it does not exist until this step runs.
  provision   — create the Retell inbound + outbound agents, PUBLISH v0 of each,
                and persist their ids. Confirms each agent shipped with end_call,
                update_lead_status and end_call_after_silence_ms wired — the
                V06/V07/V09 regression.
  twilio      — wire the number to Retell (trunk, origination, ACL, import) and
                bind BOTH agents to it: inbound and outbound.
  vercel      — deploy the client panel and set its production env.

`all` does NOT run these top to bottom: they depend on each other. It runs
validate -> secret -> deploy -> provision -> twilio -> secret -> vercel, the one
order that survives the gotchas (deploy needs the Secret; provision needs the
MODAL_URL that deploy prints; the Secret is refreshed after provision so the
runtime has the agent ids). See cmd_all for the reasoning.

Nothing here prints a credential. Reads and writes go through .env and the CLIs,
which take secrets as arguments or on stdin, never through a log line.
"""

from __future__ import annotations

import argparse
import getpass
import os
import re
import secrets
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
_ENV_PATH = _REPO_ROOT / ".env"
_ENV_EXAMPLE_PATH = _REPO_ROOT / ".env.example"
_CONFIG_PATH = _REPO_ROOT / "sofia.config.yaml"
_DASHBOARD_DIR = _REPO_ROOT / "dashboard"
_DASHBOARD_ENV_LOCAL = _DASHBOARD_DIR / ".env.local"

if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


class SetupError(RuntimeError):
    """A step could not complete. The message carries the fix, not a stack trace."""


# --------------------------------------------------------------------------
# .env helpers — parse and upsert without disturbing the rest of the file
# --------------------------------------------------------------------------


def _strip_inline_comment(value: str) -> str:
    """Drop a ` #`/`\\t#` inline comment and surrounding quotes from an .env value."""
    value = value.split(" #", 1)[0].split("\t#", 1)[0].strip()
    return value.strip("'\"")


def read_env(path: Path = _ENV_PATH) -> dict[str, str]:
    """Read KEY=VALUE pairs from an env file. Order-preserving, comments stripped."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = _strip_inline_comment(value)
    return values


def upsert_env_var(key: str, value: str, path: Path = _ENV_PATH) -> None:
    """Write one key into an env file, preserving any inline comment on that line."""
    if not path.exists():
        path.write_text(f"{key}={value}\n", encoding="utf-8")
        return

    lines = path.read_text(encoding="utf-8").splitlines()
    pattern = re.compile(rf"^{re.escape(key)}=")
    replaced = False
    for index, line in enumerate(lines):
        if pattern.match(line):
            comment = ""
            if "#" in line:
                comment = "        # " + line.split("#", 1)[1].strip()
            lines[index] = f"{key}={value}{comment}"
            replaced = True
            break
    if not replaced:
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------
# sofia.config.yaml — section-aware scalar edit that preserves comments
# --------------------------------------------------------------------------


def _trailing_comment(remainder: str) -> str:
    """Recover a trailing `# comment` from a YAML value, ignoring '#' inside quotes.

    The old value may be quoted (`"foo"  # note`) or bare (`foo  # note`). A naive
    split on '#' would treat a '#' inside the quoted value as the comment. So a
    quoted value is skipped past its closing quote before looking for the comment.
    """
    text = remainder.rstrip()
    if text.startswith(('"', "'")):
        quote = text[0]
        close = text.find(quote, 1)
        after = text[close + 1:] if close != -1 else ""
    else:
        after = text
    if "#" in after:
        return "  # " + after.split("#", 1)[1].strip()
    return ""


def set_config_scalar(section: str, key: str, value: str, *, quote: bool = True) -> bool:
    """Replace `key:`'s value inside a top-level `section:`, keeping every comment.

    A full YAML round-trip would strip the file's comments, which carry the
    reasoning for the anchor business. So this edits in place: it finds the
    section, then the key line under it, and swaps only the value — leaving any
    trailing `# comment` intact. Scoping to the section is what keeps
    `business.name` and `agent.name` (both `  name:`) from being confused.

    Returns True if a line was changed.
    """
    if not _CONFIG_PATH.exists():
        raise SetupError(f"sofia.config.yaml no existe en {_CONFIG_PATH}")

    lines = _CONFIG_PATH.read_text(encoding="utf-8").splitlines()
    rendered = f'"{value}"' if quote else value

    in_section = False
    for index, line in enumerate(lines):
        # A top-level key (no indentation) opens or closes a section.
        if re.match(r"^\S", line):
            in_section = line.startswith(f"{section}:")
            continue
        if not in_section:
            continue
        match = re.match(rf"^(\s+){re.escape(key)}:\s*(.*)$", line)
        if match:
            indent = match.group(1)
            comment = _trailing_comment(match.group(2))
            lines[index] = f"{indent}{key}: {rendered}{comment}"
            _CONFIG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return True
    return False


# --------------------------------------------------------------------------
# Step 0 — Python preflight
#
# Modal only supports Python 3.12 for this project. Discovering that at the
# `deploy` step — after the interview, the validations and the Retell agents —
# is the most expensive possible moment: everything before it has to be redone
# under a different interpreter. So it is checked FIRST, and the fix is offered
# instead of merely described.
#
# This runs under whatever interpreter the person happened to have (3.13, 3.14),
# so it must not import anything from `app/`: stdlib only.
# --------------------------------------------------------------------------

_REQUIRED_PY = (3, 12)
_VENV_DIR = _REPO_ROOT / ".venv"
_BREW_INSTALL = "brew install python@3.12"


def _venv_bin(name: str) -> Path:
    """Path to an executable inside the project venv (POSIX and Windows)."""
    if os.name == "nt":
        return _VENV_DIR / "Scripts" / f"{name}.exe"
    return _VENV_DIR / "bin" / name


def _venv_python() -> Path:
    """Path to the interpreter inside the project venv."""
    return _venv_bin("python")


def _modal_cmd() -> str:
    """The Modal CLI — prefer the project venv, fall back to PATH.

    `secret` and `deploy` shell out to `modal`. When the installer runs under
    `.venv/bin/python` WITHOUT the venv activated (which INSTALAR.md explicitly
    allows), a bare `modal` is not on PATH even though preflight installed it
    into the venv. Resolve it from the venv when it is there, so the step does
    not die with "No encontré modal" on an otherwise correct install.
    """
    cli = _venv_bin("modal")
    return str(cli) if cli.exists() else "modal"


def _interpreter_version(executable: Path | str) -> tuple[int, int] | None:
    """(major, minor) of an interpreter, or None if it cannot be run."""
    try:
        result = subprocess.run(
            [str(executable), "-c", "import sys; print(sys.version_info[0], sys.version_info[1])"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    parts = result.stdout.split()
    if len(parts) != 2:
        return None
    return int(parts[0]), int(parts[1])


def _find_python312() -> str | None:
    """Locate a 3.12 interpreter: PATH first, then the usual Homebrew prefixes."""
    candidates = ["python3.12"]
    candidates += [
        "/opt/homebrew/bin/python3.12",  # Apple Silicon
        "/usr/local/bin/python3.12",     # Intel Mac / Linuxbrew
    ]
    for candidate in candidates:
        found = shutil.which(candidate) if "/" not in candidate else (
            candidate if Path(candidate).exists() else None
        )
        if found and _interpreter_version(found) == _REQUIRED_PY:
            return found
    return None


def _ensure_pip(executable: Path) -> None:
    """Make sure the venv has pip. Some venvs (uv's, `--without-pip`) ship without it."""
    probe = subprocess.run(
        [str(executable), "-m", "pip", "--version"], capture_output=True, text=True
    )
    if probe.returncode == 0:
        return
    print("  el entorno no traía pip; lo instalo con ensurepip")
    _run(
        [str(executable), "-m", "ensurepip", "--upgrade"],
        cwd=_REPO_ROOT,
        what="instalar pip dentro del entorno virtual",
        capture=True,
    )


def cmd_preflight(args: argparse.Namespace) -> int:
    """Guarantee a 3.12 venv exists with the project installed, before step 1.

    Prints the interpreter every later command should use. With --auto-install
    it will run Homebrew for the missing 3.12; without it, it stops and hands
    over the exact command to run.
    """
    running = f"{sys.version_info[0]}.{sys.version_info[1]}"
    print(f"\nPython que está corriendo este script: {running}")

    python312 = _find_python312()
    if not python312:
        print("No encontré Python 3.12 en el sistema. Modal solo soporta 3.12 en este proyecto.")
        if not args.auto_install:
            raise SetupError(
                "Falta Python 3.12. Instálalo con:\n\n"
                f"    {_BREW_INSTALL}\n\n"
                "y vuelve a correr `python3 scripts/setup.py preflight`. (Si no usas Mac, "
                "instala 3.12 con el gestor de tu sistema o desde python.org.)"
            )
        if not shutil.which("brew"):
            raise SetupError(
                "Pediste --auto-install pero no hay Homebrew. Instálalo desde brew.sh, o "
                "instala Python 3.12 a mano y vuelve a correr este paso."
            )
        print(f"Instalando Python 3.12 con Homebrew: {_BREW_INSTALL}")
        _run(_BREW_INSTALL.split(), cwd=_REPO_ROOT, what="instalar Python 3.12 con Homebrew")
        python312 = _find_python312()
        if not python312:
            raise SetupError(
                "Homebrew terminó pero sigo sin ver `python3.12` en el PATH. Abre una "
                "terminal nueva y vuelve a correr este paso."
            )

    print(f"Python 3.12 encontrado en: {python312}")

    venv_python = _venv_python()
    venv_version = _interpreter_version(venv_python) if venv_python.exists() else None

    if venv_version and venv_version != _REQUIRED_PY:
        raise SetupError(
            f"El entorno en {_VENV_DIR} corre Python {venv_version[0]}.{venv_version[1]}, no 3.12. "
            f"Bórralo (`rm -rf {_VENV_DIR}`) y vuelve a correr este paso para recrearlo con 3.12."
        )

    if not venv_version:
        print(f"Creando el entorno virtual con 3.12 en {_VENV_DIR}...")
        _run([python312, "-m", "venv", str(_VENV_DIR)], cwd=_REPO_ROOT, what="crear el entorno virtual")
    else:
        print(f"El entorno en {_VENV_DIR} ya corre Python 3.12.")

    # `.[deploy]` and not `.`: modal is an optional dependency, and the very next
    # steps (`secret`, `deploy`) shell out to the `modal` CLI. Installing the
    # base project alone leaves a venv that looks ready and dies one step later
    # with "No encontré `modal`" — exactly the kind of manual patch-up the
    # preflight exists to remove.
    print('Instalando las dependencias del proyecto (pip install -e ".[deploy]")...')
    _ensure_pip(_venv_python())
    _run(
        [str(_venv_python()), "-m", "pip", "install", "--quiet", "--upgrade", "pip"],
        cwd=_REPO_ROOT,
        what="actualizar pip",
    )
    _run(
        [str(_venv_python()), "-m", "pip", "install", "--quiet", "-e", ".[deploy]"],
        cwd=_REPO_ROOT,
        what="instalar las dependencias del proyecto",
    )

    modal_cli = _venv_bin("modal")
    if not modal_cli.exists():
        raise SetupError(
            "Las dependencias quedaron instaladas pero no veo el CLI de `modal` en "
            f"{modal_cli}. Instálalo dentro del entorno con "
            f"`{_venv_python()} -m pip install modal` y vuelve a correr este paso."
        )
    print(f"CLI de Modal listo en el entorno: {modal_cli}")

    print("\n" + "=" * 60)
    print("PREFLIGHT OK — usa ESTE intérprete para todos los pasos siguientes:")
    print(f"  {_venv_python()}")
    print(f"  (o activa el entorno: source {_VENV_DIR}/bin/activate)")
    print("=" * 60 + "\n")
    return 0


# --------------------------------------------------------------------------
# Step 1 — interview
# --------------------------------------------------------------------------

# (env key, human label, is_secret). The full list from CLAUDE.md section 5.
_CREDENTIALS: list[tuple[str, str, bool]] = [
    ("RETELL_API_KEY", "Retell · API key", True),
    ("TWILIO_ACCOUNT_SID", "Twilio · Account SID", False),
    ("TWILIO_AUTH_TOKEN", "Twilio · Auth Token", True),
    ("TWILIO_PHONE_NUMBER", "Twilio · número en E.164 (+52...)", False),
    ("HIGHLEVEL_PIT", "GoHighLevel · Private Integration Token", True),
    ("HIGHLEVEL_LOCATION_ID", "GoHighLevel · Location id (subcuenta)", False),
    ("HIGHLEVEL_CALENDAR_ID", "GoHighLevel · Calendar id", False),
    ("HIGHLEVEL_PIPELINE_ID", "GoHighLevel · Pipeline id (Nuevos Pacientes)", False),
    ("HIGHLEVEL_STAGE_ID", "GoHighLevel · Stage id (Cita Agendada)", False),
    ("ANTHROPIC_API_KEY", "Anthropic · API key", True),
]

# MODAL_URL is deliberately NOT here. It does not exist until `modal deploy`
# prints it, so asking the person for it at interview time asks for something
# nobody can have yet. `deploy` captures it from its own output and writes it.
#
# The agent ids and DASHBOARD_API_TOKEN follow the same rule: they are produced
# by `provision` and `vercel`, never typed by hand.

# Business fields the interview offers to update in sofia.config.yaml.
# (section, key, human label, quote).
_CONFIG_BUSINESS: list[tuple[str, str, str, bool]] = [
    ("business", "name", "Nombre del negocio", True),
    ("business", "industry", "Industria (dental, inmobiliaria, ...)", False),
    ("business", "timezone", "Timezone IANA (ej. America/Cancun)", True),
    ("business", "hours", "Horario de atención (texto hablado)", True),
]

# The crm ids live in both places: the config (what ghl_service reads) and .env
# (what CLAUDE.md documents). The interview writes them to both so they can never
# disagree.
_CONFIG_CRM: list[tuple[str, str]] = [
    ("calendar_id", "HIGHLEVEL_CALENDAR_ID"),
    ("pipeline_id", "HIGHLEVEL_PIPELINE_ID"),
    ("stage_id", "HIGHLEVEL_STAGE_ID"),
]


def _prompt(label: str, *, secret: bool, current_set: bool) -> str | None:
    """Ask for one value. Empty input keeps whatever is already stored."""
    suffix = " [Enter = dejar el actual]" if current_set else ""
    prompt = f"  {label}{suffix}: "
    entered = getpass.getpass(prompt) if secret else input(prompt)
    entered = entered.strip()
    return entered or None


def cmd_interview(args: argparse.Namespace) -> int:
    """Fill .env (credentials) and sofia.config.yaml (business + crm ids).

    With --skip-interview it does not prompt: it only verifies that .env and
    sofia.config.yaml already exist, so the rest of the flow can run unattended.
    """
    if not _ENV_PATH.exists():
        if _ENV_EXAMPLE_PATH.exists():
            _ENV_PATH.write_text(_ENV_EXAMPLE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
            print("Creé .env a partir de .env.example.")
        else:
            _ENV_PATH.write_text("", encoding="utf-8")
            print("Creé un .env vacío.")

    if args.skip_interview:
        missing = [k for k, _, _ in _CREDENTIALS if not read_env().get(k)]
        if missing:
            print(f"\nCon --skip-interview asumo .env lleno, pero faltan: {missing}\n")
            return 1
        print("\n--skip-interview: .env y sofia.config.yaml ya están; sigo sin preguntar.\n")
        return 0

    print("\n=== Credenciales (se escriben en .env, nunca se imprimen) ===")
    print("Pega cada valor cuando te lo pida. Los secretos no se ven al escribir.\n")
    current = read_env()
    for key, label, is_secret in _CREDENTIALS:
        value = _prompt(label, secret=is_secret, current_set=bool(current.get(key)))
        if value is not None:
            upsert_env_var(key, value)

    print("\n=== Datos del negocio (se escriben en sofia.config.yaml) ===")
    print("Enter deja el valor actual. Ya viene precargado con la clínica ancla.\n")
    for section, key, label, quote in _CONFIG_BUSINESS:
        value = _prompt(label, secret=False, current_set=True)
        if value is not None:
            if not set_config_scalar(section, key, value, quote=quote):
                print(f"    aviso: no encontré {section}.{key} en el YAML; revísalo a mano.")

    print("\n=== IDs de GoHighLevel (se escriben en el YAML y en .env) ===")
    print("GHL es referencia: estos ids son de una subcuenta YA armada. Nada se crea.\n")
    for cfg_key, env_key in _CONFIG_CRM:
        value = _prompt(f"crm.{cfg_key}", secret=False, current_set=True)
        if value is not None:
            set_config_scalar("crm", cfg_key, value, quote=True)
            upsert_env_var(env_key, value)

    print("\nEntrevista lista. Sigue: python scripts/setup.py validate\n")
    return 0


# --------------------------------------------------------------------------
# Step 2 — validate
# --------------------------------------------------------------------------


def cmd_validate(args: argparse.Namespace) -> int:
    """Delegate to scripts/validate.py. Stops the flow if anything fails."""
    from scripts import validate

    code = validate.main(["all"])
    if code != 0:
        raise SetupError("La validación falló. Corrige lo de arriba antes de seguir.")
    return 0


# --------------------------------------------------------------------------
# Step 3 — provision Retell agents
# --------------------------------------------------------------------------

_REQUIRED_TOOLS = {"end_call", "update_lead_status"}


def _assert_agent_wiring(label: str, result: dict[str, Any]) -> None:
    """Guard the V06/V07/V09 regression: the agent must ship with its tools."""
    tools = set(result.get("tools") or [])
    missing = _REQUIRED_TOOLS - tools
    if missing:
        raise SetupError(
            f"El agente {label} salió SIN las tools {sorted(missing)}. Sofía no podría "
            f"colgar ni mover el lead. Revisa build_custom_functions en retell_service."
        )
    if not result.get("end_call_after_silence_ms"):
        raise SetupError(
            f"El agente {label} salió sin end_call_after_silence_ms. Sin ese timeout la "
            f"llamada se queda abierta consumiendo minutos. Revisa create_{label}_agent."
        )


def _assert_published(label: str, result: dict[str, Any]) -> None:
    """A provisioned agent must leave a PUBLISHED version behind.

    `agent.create` leaves an unpublished draft. The control panel edits by
    branching from the latest published version, so an agent that was never
    published makes the panel fail on first load with `source_unavailable`. The
    install is not done until the baseline exists.
    """
    if result.get("published_version") is None:
        raise SetupError(
            f"El agente {label} quedó SIN versión publicada. El panel de control no puede "
            f"editarlo así (falla con `source_unavailable`). Revisa publish_initial_version "
            f"en retell_service."
        )


def cmd_provision(args: argparse.Namespace) -> int:
    """Create the inbound and outbound agents; persist their ids to .env."""
    if not read_env().get("MODAL_URL"):
        raise SetupError(
            "MODAL_URL no está en .env. Las tools del agente apuntan al backend, así que "
            "el backend tiene que existir antes de provisionar. Corre primero "
            "`python scripts/setup.py deploy`: él la captura de la salida de Modal y la "
            "escribe solo. No la escribas a mano."
        )

    from app.services import retell_service

    print("\nCreando el agente inbound...")
    inbound = retell_service.provision_inbound()
    _assert_agent_wiring("inbound", inbound)
    _assert_published("inbound", inbound)
    print(
        f"  inbound listo: {inbound['agent_id']} · tools {inbound.get('tools')} "
        f"· publicado v{inbound.get('published_version')}"
    )

    print("Creando el agente outbound...")
    outbound = retell_service.provision_outbound()
    _assert_agent_wiring("outbound", outbound)
    _assert_published("outbound", outbound)
    print(
        f"  outbound listo: {outbound['agent_id']} · tools {outbound.get('tools')} "
        f"· publicado v{outbound.get('published_version')}"
    )

    print("\nAmbos agentes traen end_call + update_lead_status + end_call_after_silence_ms,")
    print("y quedan con una versión PUBLICADA — que es lo que el panel necesita para editar.\n")
    return 0


# --------------------------------------------------------------------------
# Step 4 — connect Twilio to Retell
# --------------------------------------------------------------------------


def cmd_twilio(args: argparse.Namespace) -> int:
    """Wire the Twilio number to Retell end to end. Safe to re-run."""
    env = read_env()
    inbound_agent = env.get("RETELL_INBOUND_AGENT_ID")
    if not inbound_agent:
        raise SetupError(
            "RETELL_INBOUND_AGENT_ID no está en .env. Corre `python scripts/setup.py provision` "
            "primero: el número se ata a ese agente."
        )

    # Optional on purpose: Retell gates outbound calls behind identity
    # verification, so an install can legitimately be inbound-only for a while.
    outbound_agent = env.get("RETELL_OUTBOUND_AGENT_ID")
    if not outbound_agent:
        print(
            "\nAviso: no hay RETELL_OUTBOUND_AGENT_ID en .env. El número queda solo de "
            "entrada; la devolución de llamadas no va a funcionar hasta atarlo."
        )

    from app.services import twilio_service

    print("\nConectando el número de Twilio a Retell...")
    result = twilio_service.connect_number_to_retell(
        inbound_agent_id=inbound_agent,
        outbound_agent_id=outbound_agent or None,
    )

    bound_outbound = (result.get("outbound") or {}).get("outbound_agent_id")
    if bound_outbound:
        print(f"  agente outbound atado al número: {bound_outbound}")

    checks = result.get("verification", {}).get("checks", [])
    for check in checks:
        mark = "OK  " if check["ok"] else "FALLA"
        print(f"  [{mark}] {check['check']}: {check['detail']}")

    if not result.get("verification", {}).get("ok", False):
        raise SetupError(
            "La verificación del número no pasó (ver arriba). Revisa el número en Twilio "
            "y que el trunk quedó adjunto. Es re-ejecutable: corrige y vuelve a correr."
        )
    print("\nNúmero conectado y verificado contra Twilio y Retell.\n")
    return 0


# --------------------------------------------------------------------------
# Step 5 — Modal Secret
# --------------------------------------------------------------------------

_MODAL_SECRET_NAME = "agente-voz-credentials"


def cmd_secret(args: argparse.Namespace) -> int:
    """Push the whole .env into the Modal Secret. --force replaces it entirely.

    Every non-empty key in .env goes in, so the runtime never misses one. The
    values are passed as CLI arguments to `modal`, never printed here.
    """
    env = {k: v for k, v in read_env().items() if v}
    if not env:
        raise SetupError("El .env no tiene valores que subir. Corre la entrevista primero.")

    command = [_modal_cmd(), "secret", "create", _MODAL_SECRET_NAME]
    command += [f"{key}={value}" for key, value in env.items()]
    if args.force:
        command.append("--force")

    # Only key names are logged — never the values.
    print(f"\nSubiendo {len(env)} llaves al Modal Secret `{_MODAL_SECRET_NAME}`: {sorted(env)}")
    _run(command, cwd=_REPO_ROOT, what="crear el Modal Secret")
    print("Modal Secret actualizado.\n")
    return 0


# --------------------------------------------------------------------------
# Step 6 — deploy the backend
# --------------------------------------------------------------------------

_MODAL_TARGET = "app/main.py::modal_app"
_MODAL_WORKER_TARGET = "app/worker.py::modal_app"
_MODAL_URL_RE = re.compile(r"https://[a-z0-9-]+\.modal\.run", re.IGNORECASE)


def cmd_deploy(args: argparse.Namespace) -> int:
    """Deploy BOTH Modal apps — the backend and the outbound worker.

    Two separate deploys, because they are two Modal apps: `app/main.py` serves
    the tools and webhooks Retell calls, and `app/worker.py` is the hourly cron
    that calls leads and no-shows back. Deploying only the first leaves the
    outbound half of the product silently absent — nothing errors, the callbacks
    simply never happen.

    The ::modal_app suffix is mandatory on both (Modal otherwise looks for a
    variable literally named `app`). The image already packs sofia.config.yaml
    and prompts/ (see app/main.py); without that the post-call analysis fails
    silently behind the webhook's 200.
    """
    print(f"\nDesplegando el backend: modal deploy {_MODAL_TARGET}")
    completed = _run(
        [_modal_cmd(), "deploy", _MODAL_TARGET],
        cwd=_REPO_ROOT,
        what="desplegar el backend a Modal",
        capture=True,
    )
    output = (completed.stdout or "") + (completed.stderr or "")
    print(output)

    match = _MODAL_URL_RE.search(output)
    if match:
        url = match.group(0)
        upsert_env_var("MODAL_URL", url)
        # Also set it in-process: the service layer's _load_env_file() never
        # overrides a var already in os.environ, and a later step in this same
        # `all` run (provision) needs MODAL_URL immediately. Writing the file is
        # not enough on its own if os.environ was seeded before this point.
        os.environ["MODAL_URL"] = url
        print(f"\nGuardé MODAL_URL={url} en .env.\n")
    else:
        print(
            "\nNo pude leer la URL del backend de la salida de Modal. Cópiala a mano a "
            "MODAL_URL en .env (y a BACKEND_URL del panel).\n"
        )

    print(f"Desplegando el worker de outbound: modal deploy {_MODAL_WORKER_TARGET}")
    worker = _run(
        [_modal_cmd(), "deploy", _MODAL_WORKER_TARGET],
        cwd=_REPO_ROOT,
        what="desplegar el worker de outbound a Modal",
        capture=True,
    )
    print((worker.stdout or "") + (worker.stderr or ""))
    print("Worker de devolución de llamadas desplegado (cron cada hora).\n")
    return 0


# --------------------------------------------------------------------------
# Step 7 — deploy the client panel to Vercel
# --------------------------------------------------------------------------

# The four production env vars the panel needs. BACKEND_URL mirrors the backend's
# MODAL_URL; DASHBOARD_API_TOKEN must match the backend's; the last two are
# generated locally if absent.
_PANEL_GENERATED = {
    "DASHBOARD_PASSWORD": 12,        # token_urlsafe length -> the client's password
    "DASHBOARD_SESSION_SECRET": 32,  # signs the session cookie; unrelated to the password
}


def ensure_dashboard_api_token() -> tuple[str, bool]:
    """The shared panel<->backend token: generate it if absent. Returns (token, generated).

    It is a generated secret, not a credential anyone can go look up, so asking
    for it would be asking for something that does not exist. It has to live in
    TWO places that must agree: the panel's Vercel env, and the Modal Secret the
    backend reads (`/dashboard` rejects any request whose token does not match).
    Generating it here — early, into .env — is what lets the normal `secret`
    step carry it to Modal like any other key.
    """
    token = read_env().get("DASHBOARD_API_TOKEN")
    if token:
        return token, False
    token = secrets.token_urlsafe(32)
    upsert_env_var("DASHBOARD_API_TOKEN", token)
    os.environ["DASHBOARD_API_TOKEN"] = token
    print("  generé DASHBOARD_API_TOKEN y lo guardé en .env (va también al Modal Secret)")
    return token, True


def _panel_env() -> tuple[dict[str, str], bool]:
    """Assemble the four production env vars, generating the three that are missing.

    Returns the vars plus whether the shared API token had to be created, so the
    caller knows the Modal Secret needs a refresh before the panel can talk to
    the backend.
    """
    root_env = read_env()
    local_env = read_env(_DASHBOARD_ENV_LOCAL)

    backend_url = root_env.get("MODAL_URL")
    if not backend_url:
        raise SetupError(
            "No hay MODAL_URL en .env, así que el panel no sabe a qué backend apuntar. "
            "Corre `python scripts/setup.py deploy` primero."
        )
    api_token, token_generated = ensure_dashboard_api_token()

    panel = {"BACKEND_URL": backend_url, "DASHBOARD_API_TOKEN": api_token}
    for key, length in _PANEL_GENERATED.items():
        value = local_env.get(key)
        if not value:
            value = secrets.token_urlsafe(length)
            upsert_env_var(key, value, path=_DASHBOARD_ENV_LOCAL)
            print(f"  generé {key} y lo guardé en dashboard/.env.local")
        panel[key] = value
    return panel, token_generated


def _set_vercel_env(key: str, value: str) -> None:
    """Set one production env var, idempotently: remove then add. Value via stdin."""
    subprocess.run(
        ["vercel", "env", "rm", key, "production", "--yes"],
        cwd=_DASHBOARD_DIR,
        capture_output=True,
        text=True,
    )  # ignore result: a first-time var has nothing to remove
    result = subprocess.run(
        ["vercel", "env", "add", key, "production"],
        cwd=_DASHBOARD_DIR,
        input=f"{value}\n",
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SetupError(f"No pude fijar {key} en Vercel: {result.stderr.strip()}")


def cmd_vercel(args: argparse.Namespace) -> int:
    """Link, set production env, and deploy the panel. Prints the URL and password."""
    if not _DASHBOARD_DIR.exists():
        raise SetupError(f"No existe el directorio del panel en {_DASHBOARD_DIR}")

    panel, token_generated = _panel_env()

    if token_generated:
        # The backend has to learn the token BEFORE the panel starts calling it,
        # or every /dashboard request comes back 401 on a fresh install.
        print("\nEl token del panel es nuevo: refresco el Modal Secret para que el backend lo tenga.")
        cmd_secret(argparse.Namespace(force=True))

    print("\nEnlazando el proyecto de Vercel (necesitas `vercel login` hecho)...")
    _run(["vercel", "link", "--yes"], cwd=_DASHBOARD_DIR, what="enlazar el proyecto de Vercel")

    print("Fijando las 4 variables de producción del panel...")
    for key, value in panel.items():
        _set_vercel_env(key, value)
        print(f"  [OK] {key}")  # value never printed

    print("Desplegando el panel a producción...")
    completed = _run(
        ["vercel", "--prod", "--yes"],
        cwd=_DASHBOARD_DIR,
        what="desplegar el panel a Vercel",
        capture=True,
    )
    output = (completed.stdout or "") + (completed.stderr or "")
    url_match = re.search(r"https://[^\s]+\.vercel\.app", output)
    panel_url = url_match.group(0) if url_match else "(revisa la salida de Vercel arriba)"

    print("\n" + "=" * 60)
    print("PANEL DESPLEGADO")
    print(f"  URL:         {panel_url}")
    print(f"  Contraseña:  {panel['DASHBOARD_PASSWORD']}")
    print("  (guardada en dashboard/.env.local — entrégala al cliente por un canal seguro)")
    print("=" * 60 + "\n")
    return 0


# --------------------------------------------------------------------------
# `all` — steps 2 through 7 in order
# --------------------------------------------------------------------------


def cmd_all(args: argparse.Namespace) -> int:
    """Run the steps in DEPENDENCY order, not in subcommand-listing order.

    On a fresh account the pieces depend on each other in a way the plain 1..7
    listing does not reflect, so `all` cannot just run them top to bottom:

      - `deploy` needs the Modal Secret to already exist (main.py references
        `Secret.from_name`), so `secret` must come BEFORE `deploy`.
      - `provision` needs MODAL_URL (the Retell tools point at the backend), and
        only `deploy` produces it, so `provision` must come AFTER `deploy`.
      - the backend's worker and dashboard read the Retell agent ids at runtime
        from the Secret, so `secret` runs a SECOND time after `provision` to bake
        in the ids it just created. Both secret writes are idempotent (--force).
      - `validate` runs FIRST but tolerates the agents not existing yet — it
        checks the Retell API key, not an agent id that only `provision` can
        create. Demanding the id here is what used to deadlock the install.
      - DASHBOARD_API_TOKEN is generated up front, before the first `secret`, so
        the backend and the panel end up sharing the same value without a
        third Secret write.

    Order: token -> validate -> secret -> deploy -> provision -> twilio ->
    secret -> vercel.
    """
    print("\n########## paso: token del panel ##########")
    ensure_dashboard_api_token()

    steps: list[tuple[str, Callable[[argparse.Namespace], int]]] = [
        ("validate", cmd_validate),
        ("secret", cmd_secret),
        ("deploy (backend + worker)", cmd_deploy),
        ("provision", cmd_provision),
        ("twilio", cmd_twilio),
        ("secret (refresh con los agent ids)", cmd_secret),
        ("vercel", cmd_vercel),
    ]
    for name, func in steps:
        print(f"\n########## paso: {name} ##########")
        func(args)
    print("\nInstalación completa.\n")
    return 0


# --------------------------------------------------------------------------
# Subprocess helper
# --------------------------------------------------------------------------


def _run(
    command: list[str],
    *,
    cwd: Path,
    what: str,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run a CLI command, raising a SetupError with context if it fails."""
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            text=True,
            capture_output=capture,
            check=True,
        )
    except FileNotFoundError as exc:
        raise SetupError(
            f"No encontré `{command[0]}`. Instálalo y asegúrate de que esté en el PATH "
            f"antes de {what}."
        ) from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip() if capture else "(ver salida arriba)"
        raise SetupError(f"Falló al {what}: {detail}") from exc
    return result


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Instalador de Sofía — un subcomando por paso, todos idempotentes."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_preflight = sub.add_parser(
        "preflight", help="Paso 0: verifica Python 3.12, crea el venv e instala las deps."
    )
    p_preflight.add_argument(
        "--auto-install",
        action="store_true",
        help="Si falta Python 3.12, instálalo con Homebrew en vez de solo decir cómo.",
    )
    p_preflight.set_defaults(func=cmd_preflight)

    p_interview = sub.add_parser("interview", help="Pide credenciales y llena .env + config.")
    p_interview.add_argument(
        "--skip-interview",
        action="store_true",
        help="No preguntes: asume .env y sofia.config.yaml ya llenos.",
    )
    p_interview.set_defaults(func=cmd_interview)

    sub.add_parser("validate", help="Valida cada credencial contra su API.").set_defaults(
        func=cmd_validate
    )
    sub.add_parser("provision", help="Crea los agentes de Retell.").set_defaults(func=cmd_provision)
    sub.add_parser("twilio", help="Conecta el número de Twilio a Retell.").set_defaults(
        func=cmd_twilio
    )

    p_secret = sub.add_parser("secret", help="Crea/actualiza el Modal Secret desde .env.")
    p_secret.add_argument(
        "--force",
        action="store_true",
        help="Reemplaza el secreto ENTERO (recomendado, para no perder llaves).",
    )
    p_secret.set_defaults(func=cmd_secret)

    sub.add_parser(
        "deploy", help="Despliega el backend Y el worker de outbound a Modal."
    ).set_defaults(func=cmd_deploy)
    sub.add_parser("vercel", help="Despliega el panel a Vercel.").set_defaults(func=cmd_vercel)

    p_all = sub.add_parser("all", help="Corre los pasos 2->7 en orden.")
    p_all.add_argument("--force", action="store_true", help="Pasa --force al paso del Modal Secret.")
    p_all.add_argument("--skip-interview", action="store_true", help="No-op aquí; `all` no entrevista.")
    p_all.set_defaults(func=cmd_all)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    # `all` may pass --force to the secret step; default it for direct subcommands.
    if not hasattr(args, "force"):
        args.force = False
    if not hasattr(args, "skip_interview"):
        args.skip_interview = False
    if not hasattr(args, "auto_install"):
        args.auto_install = False
    try:
        return args.func(args)
    except SetupError as exc:
        print(f"\nERROR: {exc}\n", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
