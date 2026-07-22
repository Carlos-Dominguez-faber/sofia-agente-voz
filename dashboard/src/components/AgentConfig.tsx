"use client";

/**
 * Configuración de Sofía — voice and behaviour, changed live.
 *
 * This is the section that turns the panel from a viewer into a control panel.
 * Every control here is bounded so a clinic owner cannot break Sofía: the voice
 * is a curated dropdown, the speed a clamped slider, the behaviour three named
 * presets. The raw numbers (temperature) are never shown — the client picks
 * "Estricta / Balanceada / Flexible", not "0.35".
 *
 * Saving does NOT leave the change in a Retell draft. The backend runs
 * create_version → update → publish so the new voice/behaviour is what the real
 * phone number serves, on both the inbound and outbound agents. "Llámame para
 * probar" is how the owner hears it a moment later.
 */

import { useState } from "react";

import { branding } from "@/config/branding";
import type { AgentConfig as Config } from "@/lib/types";

type SaveState =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "saved"; message: string }
  | { kind: "error"; message: string };

const BEHAVIOUR_LABELS: Record<string, { title: string; hint: string }> = {
  estricta: { title: "Estricta", hint: "Se ciñe al guion, mínima improvisación" },
  balanceada: { title: "Balanceada", hint: "Natural pero enfocada — la recomendada" },
  flexible: { title: "Flexible", hint: "Más conversacional y espontánea" },
};

export function AgentConfig({ config }: { config: Config }) {
  const [voiceId, setVoiceId] = useState(config.voice_id ?? config.curated_voices[0]?.voice_id ?? "");
  const [speed, setSpeed] = useState(config.voice_speed ?? 1);
  const [expressive, setExpressive] = useState(config.expressiveness ?? true);
  const [behaviour, setBehaviour] = useState(config.behaviour ?? "balanceada");
  const [state, setState] = useState<SaveState>({ kind: "idle" });

  const dirty =
    voiceId !== config.voice_id ||
    speed !== config.voice_speed ||
    expressive !== config.expressiveness ||
    behaviour !== config.behaviour;

  async function save() {
    setState({ kind: "saving" });
    const response = await fetch("/api/agent/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        voice_id: voiceId,
        voice_speed: speed,
        expressiveness: expressive,
        behaviour,
      }),
    });
    const payload = await response.json().catch(() => null);
    if (response.ok && payload?.ok) {
      setState({
        kind: "saved",
        message: `${branding.agentName} ya suena así en las llamadas. Usa "Llámame para probar" para escucharla.`,
      });
    } else {
      setState({ kind: "error", message: payload?.message ?? "No pude aplicar el cambio." });
    }
  }

  return (
    <div className="space-y-6">
      {/* Voz */}
      <div>
        <label htmlFor="voice" className="block text-sm font-medium text-slate-700 dark:text-slate-300">
          Voz
        </label>
        <select
          id="voice"
          value={voiceId}
          onChange={(event) => setVoiceId(event.target.value)}
          className="mt-1 w-full max-w-sm rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-teal-500 focus:ring-1 focus:ring-teal-500 focus:outline-none dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100"
        >
          {config.curated_voices.map((voice) => (
            <option key={voice.voice_id} value={voice.voice_id}>
              {voice.label} — {voice.note}
            </option>
          ))}
        </select>
      </div>

      {/* Velocidad */}
      <div>
        <label htmlFor="speed" className="block text-sm font-medium text-slate-700 dark:text-slate-300">
          Velocidad al hablar
        </label>
        <div className="mt-1 flex items-center gap-3">
          <span className="text-xs text-slate-400">Pausada</span>
          <input
            id="speed"
            type="range"
            min={config.speed_min}
            max={config.speed_max}
            step={0.05}
            value={speed}
            onChange={(event) => setSpeed(Number(event.target.value))}
            className="h-2 max-w-xs flex-1 cursor-pointer accent-teal-600"
          />
          <span className="text-xs text-slate-400">Ágil</span>
        </div>
      </div>

      {/* Expresividad */}
      <div className="flex items-center justify-between max-w-sm">
        <div>
          <p className="text-sm font-medium text-slate-700 dark:text-slate-300">Expresividad</p>
          <p className="text-xs text-slate-400">Cuánta emoción pone en la voz</p>
        </div>
        <button
          type="button"
          role="switch"
          aria-checked={expressive}
          onClick={() => setExpressive((value) => !value)}
          className={`relative h-6 w-11 rounded-full transition-colors ${expressive ? "bg-teal-600" : "bg-slate-300 dark:bg-slate-700"}`}
        >
          <span
            className={`absolute top-0.5 h-5 w-5 rounded-full bg-white transition-transform ${expressive ? "translate-x-5" : "translate-x-0.5"}`}
          />
        </button>
      </div>

      {/* Comportamiento */}
      <div>
        <p className="text-sm font-medium text-slate-700 dark:text-slate-300">Comportamiento</p>
        <p className="mb-2 text-xs text-slate-400">Qué tanto se apega al guion</p>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
          {config.presets.map((preset) => {
            const meta = BEHAVIOUR_LABELS[preset] ?? { title: preset, hint: "" };
            const selected = behaviour === preset;
            return (
              <button
                key={preset}
                type="button"
                onClick={() => setBehaviour(preset)}
                className={`rounded-lg border p-3 text-left transition-colors ${
                  selected
                    ? "border-teal-500 bg-teal-50 dark:border-teal-500 dark:bg-teal-950/40"
                    : "border-slate-200 hover:border-slate-300 dark:border-slate-800 dark:hover:border-slate-700"
                }`}
              >
                <p className="text-sm font-medium text-slate-800 dark:text-slate-200">{meta.title}</p>
                <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">{meta.hint}</p>
              </button>
            );
          })}
        </div>
      </div>

      {/* Guardar */}
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={save}
          disabled={!dirty || state.kind === "saving"}
          className="rounded-md bg-teal-600 px-4 py-2 text-sm font-medium text-white hover:bg-teal-700 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {state.kind === "saving" ? "Aplicando…" : "Guardar y publicar"}
        </button>
        {dirty ? (
          <span className="text-xs text-slate-400 dark:text-slate-500">Cambios sin aplicar</span>
        ) : null}
        {config.synced_agents.length > 1 ? (
          <span className="text-xs text-slate-400 dark:text-slate-500">
            Se aplica a las llamadas entrantes y salientes.
          </span>
        ) : null}
      </div>

      {state.kind === "saved" ? (
        <p className="rounded-lg border border-emerald-300 bg-emerald-50 p-3 text-sm text-emerald-800 dark:border-emerald-800/60 dark:bg-emerald-950/30 dark:text-emerald-300">
          {state.message}
        </p>
      ) : null}
      {state.kind === "error" ? (
        <p className="rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-800 dark:border-red-800/60 dark:bg-red-950/30 dark:text-red-300">
          {state.message}
        </p>
      ) : null}
    </div>
  );
}
