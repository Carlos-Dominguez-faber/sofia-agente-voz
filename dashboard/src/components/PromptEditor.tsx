"use client";

/**
 * Editing Sofía's prompt without ever opening Retell.
 *
 * This is the commercial heart of the panel — and the riskiest thing in it.
 * The prompt carries the medical safety rules: never diagnose, never recommend
 * medication, never confirm an appointment the system did not create.
 *
 * Those rules are not in this textarea. The backend replaces them with a marker
 * before sending the prompt here, and puts the reviewed version back on save.
 * They are shown below, read-only, so the person editing can see what is
 * protecting them rather than wondering what the marker means.
 *
 * If the marker is deleted anyway, the save is refused with the backend's own
 * explanation. Refused, not warned: a warning that can be clicked past is not a
 * barrier.
 */

import { useState } from "react";

import type { PromptPayload } from "@/lib/types";

type Status =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "saved"; message: string }
  | { kind: "refused"; message: string };

export function PromptEditor({ prompt }: { prompt: PromptPayload }) {
  const [text, setText] = useState(prompt.editable);
  const [status, setStatus] = useState<Status>({ kind: "idle" });
  const [canUndo, setCanUndo] = useState(prompt.previous.available);

  const dirty = text !== prompt.editable;
  const markerPresent = text.includes(prompt.protection.marker);

  async function save() {
    setStatus({ kind: "saving" });
    const response = await fetch("/api/agent/prompt", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ editable: text }),
    });
    const payload = await response.json().catch(() => null);

    if (response.ok && payload?.ok) {
      setStatus({ kind: "saved", message: payload.message ?? "Prompt guardado." });
      setCanUndo(true);
    } else {
      setStatus({
        kind: "refused",
        message: payload?.message ?? "No pude guardar el prompt.",
      });
    }
  }

  async function undo() {
    setStatus({ kind: "saving" });
    const response = await fetch("/api/agent/prompt/undo", { method: "POST" });
    const payload = await response.json().catch(() => null);
    if (response.ok && payload?.ok) {
      setStatus({ kind: "saved", message: "Restauré la versión anterior. Recarga para verla." });
    } else {
      setStatus({ kind: "refused", message: payload?.message ?? "No pude restaurar." });
    }
  }

  function restoreMarker() {
    setText((current) => `${current.trimEnd()}\n\n${prompt.protection.marker}\n`);
    setStatus({ kind: "idle" });
  }

  return (
    <div className="space-y-3">
      {!markerPresent ? (
        <div className="rounded-lg border border-red-300 bg-red-50 p-3 text-sm dark:border-red-800/60 dark:bg-red-950/30">
          <p className="font-medium text-red-900 dark:text-red-200">
            Borraste el bloque de reglas de seguridad
          </p>
          <p className="mt-1 text-red-800 dark:text-red-300">
            Sin él no se puede guardar. Esas reglas impiden que {""}
            Sofía dé un diagnóstico o confirme una cita que no existe.
          </p>
          <button
            type="button"
            onClick={restoreMarker}
            className="mt-2 rounded-md bg-red-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-red-700"
          >
            Restaurar el bloque
          </button>
        </div>
      ) : null}

      <textarea
        value={text}
        onChange={(event) => setText(event.target.value)}
        spellCheck={false}
        rows={20}
        aria-label="Prompt de Sofía"
        className="w-full rounded-lg border border-slate-300 bg-white p-3 font-mono text-xs leading-relaxed text-slate-800 focus:border-teal-500 focus:ring-1 focus:ring-teal-500 focus:outline-none dark:border-slate-700 dark:bg-slate-950 dark:text-slate-200"
      />

      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={save}
          disabled={!dirty || status.kind === "saving"}
          className="rounded-md bg-teal-600 px-4 py-2 text-sm font-medium text-white hover:bg-teal-700 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {status.kind === "saving" ? "Guardando…" : "Guardar cambios"}
        </button>

        {canUndo ? (
          <button
            type="button"
            onClick={undo}
            disabled={status.kind === "saving"}
            className="rounded-md border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-40 dark:border-slate-700 dark:text-slate-200 dark:hover:bg-slate-800"
          >
            Restaurar versión anterior
          </button>
        ) : null}

        {dirty ? (
          <span className="text-xs text-slate-400 dark:text-slate-500">Cambios sin guardar</span>
        ) : null}
      </div>

      {status.kind === "saved" ? (
        <p className="rounded-lg border border-emerald-300 bg-emerald-50 p-3 text-sm text-emerald-800 dark:border-emerald-800/60 dark:bg-emerald-950/30 dark:text-emerald-300">
          {status.message}
        </p>
      ) : null}

      {status.kind === "refused" ? (
        <p className="rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-800 dark:border-red-800/60 dark:bg-red-950/30 dark:text-red-300">
          {status.message}
        </p>
      ) : null}

      <details className="rounded-lg border border-slate-200 dark:border-slate-800">
        <summary className="cursor-pointer p-3 text-sm font-medium text-slate-700 dark:text-slate-200">
          Reglas de seguridad (no editables)
        </summary>
        <div className="border-t border-slate-200 p-3 dark:border-slate-800">
          <p className="mb-2 text-sm text-slate-500 dark:text-slate-400">
            {prompt.protection.why}
          </p>
          <pre className="max-h-64 overflow-auto rounded bg-slate-50 p-3 text-xs leading-relaxed whitespace-pre-wrap text-slate-600 dark:bg-slate-950 dark:text-slate-400">
            {prompt.protection.guardrails || "No pude leer el bloque protegido."}
          </pre>
        </div>
      </details>

      {!prompt.previous.durable && prompt.previous.available ? (
        <p className="text-xs text-amber-600 dark:text-amber-400">
          La versión anterior está guardada solo en memoria y podría perderse si el backend se
          reinicia.
        </p>
      ) : null}
    </div>
  );
}
