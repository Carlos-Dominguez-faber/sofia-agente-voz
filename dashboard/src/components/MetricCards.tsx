/**
 * The four numbers the clinic reads first thing in the morning.
 *
 * "Citas agendadas por Sofía" is labelled that way on purpose. It counts
 * successful `book_appointment` tool calls, not every appointment in the
 * calendar — the receptionist also books by hand, and crediting Sofía with
 * those would inflate the success rate that justifies what she costs.
 */

import { count, duration, percent } from "@/lib/format";
import type { Metrics } from "@/lib/types";

function Card({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900">
      <p className="text-sm text-slate-500 dark:text-slate-400">{label}</p>
      <p className="mt-1 text-3xl font-semibold tabular-nums text-slate-900 dark:text-slate-100">
        {value}
      </p>
      {hint ? <p className="mt-1 text-xs text-slate-400 dark:text-slate-500">{hint}</p> : null}
    </div>
  );
}

export function MetricCards({ metrics }: { metrics: Metrics }) {
  const noCallsYet = metrics.total_calls === 0;

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <Card label="Llamadas totales" value={count(metrics.total_calls)} />
      <Card label={metrics.appointments_label} value={count(metrics.appointments_booked)} />
      <Card
        label="Tasa de éxito"
        value={percent(metrics.success_rate)}
        /* Not 0%: with no calls there is nothing to succeed at, and 0% reads as
           failure rather than as absence. */
        hint={noCallsYet ? "Sin llamadas en este periodo" : undefined}
      />
      <Card
        label="Duración promedio"
        value={duration(metrics.avg_duration_seconds)}
        hint={
          metrics.avg_duration_is_sample
            ? `Muestra de ${metrics.avg_duration_sample_size} llamadas`
            : undefined
        }
      />
    </div>
  );
}
