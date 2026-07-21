/**
 * Lead temperature — hot / warm / cold, from the tags the post-call analysis
 * wrote onto each contact in the CRM.
 */

import { branding } from "@/config/branding";
import { count } from "@/lib/format";
import type { Temperature } from "@/lib/types";

const LABELS: Record<string, { title: string; hint: string; color: keyof typeof branding.colors }> = {
  hot: { title: "Caliente", hint: "Urgencia o interés alto", color: "hot" },
  warm: { title: "Tibio", hint: "Interesado, sin prisa", color: "warm" },
  cold: { title: "Frío", hint: "Solo preguntaba", color: "cold" },
};

export function LeadTemperature({ temperature }: { temperature: Temperature }) {
  const entries = Object.entries(temperature.counts);

  if (entries.length === 0) {
    return (
      <p className="text-sm text-slate-500 dark:text-slate-400">
        No hay etiquetas de temperatura configuradas.
      </p>
    );
  }

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
      {entries.map(([key, value]) => {
        const meta = LABELS[key] ?? { title: key, hint: "", color: "cold" as const };
        return (
          <div
            key={key}
            className="flex items-center gap-3 rounded-lg border border-slate-200 p-3 dark:border-slate-800"
          >
            <span
              aria-hidden
              className="h-9 w-1.5 shrink-0 rounded-full"
              style={{ backgroundColor: branding.colors[meta.color] }}
            />
            <div className="min-w-0">
              <p className="text-2xl font-semibold tabular-nums text-slate-900 dark:text-slate-100">
                {count(value)}
              </p>
              <p className="text-sm font-medium text-slate-700 dark:text-slate-300">{meta.title}</p>
              {meta.hint ? (
                <p className="truncate text-xs text-slate-400 dark:text-slate-500">{meta.hint}</p>
              ) : null}
            </div>
          </div>
        );
      })}
    </div>
  );
}
