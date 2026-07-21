/**
 * Are the five pieces alive?
 *
 * Shown so that when a number looks wrong, the first question — "is something
 * down?" — has an answer on the same screen instead of a support call.
 */

import type { ServiceStatus } from "@/lib/types";

const LABELS: Record<string, string> = {
  gohighlevel: "CRM y calendario",
  retell: "Voz",
  twilio: "Línea telefónica",
  anthropic: "Análisis de llamadas",
  backend: "Servidor",
};

export function ServicesStatus({ status }: { status: ServiceStatus }) {
  return (
    <div className="space-y-2">
      {status.services.map((service) => (
        <div key={service.service} className="flex items-start gap-2.5 text-sm">
          <span
            aria-hidden
            className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${service.ok ? "bg-emerald-500" : "bg-red-500"}`}
          />
          <div className="min-w-0">
            <span className="text-slate-700 dark:text-slate-300">
              {LABELS[service.service] ?? service.service}
            </span>
            <span className="sr-only">{service.ok ? " funcionando" : " con problema"}</span>
            {!service.ok && service.detail ? (
              <p className="font-mono text-xs break-words text-red-600 dark:text-red-400">
                {service.detail}
              </p>
            ) : null}
          </div>
        </div>
      ))}

      {!status.all_ok ? (
        <p className="pt-1 text-xs text-amber-600 dark:text-amber-400">
          Mientras un servicio esté caído, algunos datos de este panel pueden faltar.
        </p>
      ) : null}
    </div>
  );
}
