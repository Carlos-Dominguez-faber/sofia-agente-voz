"use client";

/**
 * Recent calls. A row expands into the transcript and what the analysis found.
 *
 * Two things this table refuses to assume:
 *
 *  - that every call has a phone number. A `web_call` — which is every call
 *    until the Twilio number is connected — has none, and a caller who hung up
 *    before giving one has none either. Those rows say "Sin identificar"
 *    rather than rendering an empty cell that looks like a bug.
 *  - that the CRM answered. When GoHighLevel is unreachable the table still
 *    lists the calls, with a banner saying names and summaries are missing.
 *    A partial table is more useful than an error page.
 */

import { useEffect, useState } from "react";

import { dateTime, duration, originLabel, urgencyTone } from "@/lib/format";
import type { CallDetail, CallList, CallRow } from "@/lib/types";

function BookedBadge({ booked }: { booked: boolean }) {
  return booked ? (
    <span className="inline-flex rounded-full bg-emerald-100 px-2 py-0.5 text-xs font-medium text-emerald-800 dark:bg-emerald-950/50 dark:text-emerald-300">
      Agendó
    </span>
  ) : (
    <span className="inline-flex rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-500 dark:bg-slate-800 dark:text-slate-400">
      No agendó
    </span>
  );
}

function Detail({ callId }: { callId: string }) {
  const [detail, setDetail] = useState<CallDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    fetch(`/api/calls/${encodeURIComponent(callId)}`)
      .then(async (response) => {
        const payload = await response.json();
        if (!active) return;
        if (payload.ok) setDetail(payload.data);
        else setError(payload.message ?? "No pude cargar el detalle.");
      })
      .catch(() => active && setError("No pude cargar el detalle."))
      .finally(() => active && setLoading(false));
    // Collapsing the row before the fetch lands must not set state on a gone component.
    return () => {
      active = false;
    };
  }, [callId]);

  if (loading) {
    return (
      <p className="p-4 text-sm text-slate-500 dark:text-slate-400">
        Cargando detalle…
      </p>
    );
  }
  if (error || !detail) {
    return (
      <p className="p-4 text-sm text-amber-700 dark:text-amber-400">
        {error ?? "No pude cargar el detalle."}
      </p>
    );
  }

  return (
    <div className="space-y-4 border-t border-slate-200 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-950/40">
      {detail.analysis.resumen ? (
        <div>
          <h4 className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
            Resumen
          </h4>
          <p className="mt-1 text-sm text-slate-700 dark:text-slate-300">
            {detail.analysis.resumen}
          </p>
        </div>
      ) : (
        <p className="text-sm text-slate-500 dark:text-slate-400">
          Esta llamada no tiene resumen. Ocurre cuando quien llamó colgó antes
          de dar sus datos.
        </p>
      )}

      <div className="flex flex-wrap gap-4 text-sm">
        {detail.analysis.interes_score != null ? (
          <span className="text-slate-600 dark:text-slate-300">
            Interés: <strong>{String(detail.analysis.interes_score)}/10</strong>
          </span>
        ) : null}
        {detail.analysis.probabilidad_asistir != null ? (
          <span className="text-slate-600 dark:text-slate-300">
            Probabilidad de asistir:{" "}
            <strong>{String(detail.analysis.probabilidad_asistir)}/10</strong>
          </span>
        ) : null}
        {detail.analysis.motivo ? (
          <span className="text-slate-600 dark:text-slate-300">
            Motivo: <strong>{detail.analysis.motivo}</strong>
          </span>
        ) : null}
      </div>

      {detail.has_recording ? (
        <div>
          <h4 className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
            Grabación
          </h4>
          {/* Patient audio, streamed through the token-gated backend proxy — the
              raw Retell URL never reaches the browser. Loads only for someone
              already past the panel's password gate. */}
          <audio
            controls
            preload="none"
            src={`/api/calls/${encodeURIComponent(detail.call_id)}/recording`}
            className="mt-1 w-full max-w-md"
          >
            Tu navegador no puede reproducir este audio.
          </audio>
          <p className="mt-1 text-xs text-slate-400 dark:text-slate-500">
            Audio de una llamada real. Trátalo como información del paciente.
          </p>
        </div>
      ) : null}

      {detail.tool_calls.length > 0 ? (
        <div>
          <h4 className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
            Qué hizo el sistema
          </h4>
          <ul className="mt-1 space-y-1">
            {detail.tool_calls.map((tool, index) => (
              <li key={index} className="flex items-center gap-2 text-sm">
                <span
                  aria-hidden
                  className={
                    tool.succeeded === true
                      ? "h-2 w-2 rounded-full bg-emerald-500"
                      : tool.succeeded === false
                        ? "h-2 w-2 rounded-full bg-red-500"
                        : "h-2 w-2 rounded-full bg-slate-400"
                  }
                />
                <span className="font-mono text-slate-700 dark:text-slate-300">
                  {tool.name}
                </span>
                <span className="text-slate-400 dark:text-slate-500">
                  {tool.succeeded === true
                    ? "ok"
                    : tool.succeeded === false
                      ? "falló"
                      : "sin respuesta"}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <details>
        <summary className="cursor-pointer text-xs font-semibold uppercase tracking-wide text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200">
          Transcripción completa
        </summary>
        <pre className="mt-2 max-h-80 overflow-auto rounded bg-white p-3 text-xs leading-relaxed whitespace-pre-wrap text-slate-700 dark:bg-slate-900 dark:text-slate-300">
          {detail.transcript || "Sin transcripción."}
        </pre>
      </details>
    </div>
  );
}

function Row({ call }: { call: CallRow }) {
  const [open, setOpen] = useState(false);

  return (
    <>
      <tr
        className="cursor-pointer border-t border-slate-100 hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/40"
        onClick={() => setOpen((value) => !value)}
      >
        <td className="px-3 py-2.5">
          <button
            type="button"
            aria-expanded={open}
            className="text-left text-sm font-medium text-slate-900 dark:text-slate-100"
          >
            {call.contact_name || call.phone || (
              <span className="text-slate-400 dark:text-slate-500">
                Sin identificar
              </span>
            )}
          </button>
        </td>
        <td className="px-3 py-2.5 text-sm whitespace-nowrap text-slate-500 dark:text-slate-400">
          {dateTime(call.started_at)}
        </td>
        <td className="px-3 py-2.5 text-sm whitespace-nowrap text-slate-500 dark:text-slate-400">
          {originLabel(call.origin)}
        </td>
        <td className="px-3 py-2.5 text-sm whitespace-nowrap tabular-nums text-slate-500 dark:text-slate-400">
          {duration(call.duration_seconds)}
        </td>
        <td className="px-3 py-2.5">
          <BookedBadge booked={call.booked} />
        </td>
        <td className="px-3 py-2.5">
          {call.nivel_urgencia ? (
            <span
              className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${urgencyTone(call.nivel_urgencia)}`}
            >
              {call.nivel_urgencia}
            </span>
          ) : (
            <span className="text-slate-300 dark:text-slate-600">—</span>
          )}
        </td>
        <td className="hidden max-w-md px-3 py-2.5 text-sm text-slate-500 lg:table-cell dark:text-slate-400">
          <span className="line-clamp-2">{call.resumen || "—"}</span>
        </td>
      </tr>
      {open ? (
        <tr>
          <td colSpan={7} className="p-0">
            <Detail callId={call.call_id} />
          </td>
        </tr>
      ) : null}
    </>
  );
}

export function RecentCalls({ list }: { list: CallList }) {
  const crmDown = list.sources.ghl && list.sources.ghl !== "ok";

  if (list.calls.length === 0) {
    return (
      <p className="rounded-lg border border-slate-200 p-4 text-sm text-slate-500 dark:border-slate-800 dark:text-slate-400">
        No hubo llamadas en este periodo. (Esto sí es un dato: la consulta
        respondió correctamente.)
      </p>
    );
  }

  return (
    <div className="space-y-3">
      {crmDown ? (
        <p className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-800 dark:border-amber-800/60 dark:bg-amber-950/30 dark:text-amber-300">
          El CRM no respondió, así que faltan nombres y resúmenes. Las llamadas
          y sus duraciones sí son correctas.
        </p>
      ) : null}

      <div className="overflow-x-auto">
        <table className="w-full min-w-[720px] text-left">
          <thead>
            <tr className="text-xs uppercase tracking-wide text-slate-400 dark:text-slate-500">
              <th className="px-3 pb-2 font-medium">Paciente</th>
              <th className="px-3 pb-2 font-medium">Fecha</th>
              <th className="px-3 pb-2 font-medium">Origen</th>
              <th className="px-3 pb-2 font-medium">Duración</th>
              <th className="px-3 pb-2 font-medium">Resultado</th>
              <th className="px-3 pb-2 font-medium">Urgencia</th>
              <th className="hidden px-3 pb-2 font-medium lg:table-cell">
                Resumen
              </th>
            </tr>
          </thead>
          <tbody>
            {list.calls.map((call) => (
              <Row key={call.call_id} call={call} />
            ))}
          </tbody>
        </table>
      </div>

      <p className="text-xs text-slate-400 dark:text-slate-500">
        Toca un renglón para ver la transcripción completa.
      </p>
    </div>
  );
}
