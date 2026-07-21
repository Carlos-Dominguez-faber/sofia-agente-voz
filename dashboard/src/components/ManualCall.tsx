"use client";

/**
 * Ring a patient now.
 *
 * The only control in this panel that reaches the outside world, so it says
 * clearly what it is about to do and does not fire twice while the first
 * request is still in flight.
 */

import { useState } from "react";

type Status =
  | { kind: "idle" }
  | { kind: "calling" }
  | { kind: "started"; message: string }
  | { kind: "failed"; message: string };

export function ManualCall({ agentName }: { agentName: string }) {
  const [phone, setPhone] = useState("");
  const [status, setStatus] = useState<Status>({ kind: "idle" });

  async function call(event: React.FormEvent) {
    event.preventDefault();
    if (!phone.trim() || status.kind === "calling") return;

    setStatus({ kind: "calling" });
    const response = await fetch("/api/outbound/call", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ phone: phone.trim() }),
    });
    const payload = await response.json().catch(() => null);

    if (response.ok && payload?.ok) {
      setStatus({ kind: "started", message: payload.message ?? "Llamada iniciada." });
      setPhone("");
    } else {
      setStatus({ kind: "failed", message: payload?.message ?? "No pude iniciar la llamada." });
    }
  }

  return (
    <form onSubmit={call} className="space-y-3">
      <div className="flex flex-wrap gap-2">
        <input
          type="tel"
          value={phone}
          onChange={(event) => setPhone(event.target.value)}
          placeholder="+52 998 123 4567"
          aria-label="Teléfono del paciente"
          className="min-w-56 flex-1 rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-teal-500 focus:ring-1 focus:ring-teal-500 focus:outline-none dark:border-slate-700 dark:bg-slate-950 dark:text-slate-200"
        />
        <button
          type="submit"
          disabled={!phone.trim() || status.kind === "calling"}
          className="rounded-md bg-teal-600 px-4 py-2 text-sm font-medium text-white hover:bg-teal-700 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {status.kind === "calling" ? "Llamando…" : "Llamar ahora"}
        </button>
      </div>

      <p className="text-xs text-slate-400 dark:text-slate-500">
        {agentName} marcará este número de inmediato. Escríbelo con lada.
      </p>

      {status.kind === "started" ? (
        <p className="rounded-lg border border-emerald-300 bg-emerald-50 p-3 text-sm text-emerald-800 dark:border-emerald-800/60 dark:bg-emerald-950/30 dark:text-emerald-300">
          {status.message}
        </p>
      ) : null}

      {status.kind === "failed" ? (
        <p className="rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-800 dark:border-red-800/60 dark:bg-red-950/30 dark:text-red-300">
          {status.message}
        </p>
      ) : null}
    </form>
  );
}
