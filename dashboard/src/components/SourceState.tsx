/**
 * How a section says "I could not get this".
 *
 * This component is where the project's honesty rule becomes visible. A panel
 * that renders 0 while a source is unreachable tells the clinic owner that
 * Sofía answered nothing and booked nothing — and that reads as a broken
 * product, not as a broken query. Costlier still: it reads as *true*.
 *
 * So an unavailable source shows what failed and why, in the same space the
 * number would have occupied. Never a zero, never a blank card.
 */

import type { ReactNode } from "react";

import type { Result } from "@/lib/types";

export function Unavailable({ message, detail }: { message: string; detail?: string }) {
  return (
    <div
      role="status"
      className="rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm dark:border-amber-800/60 dark:bg-amber-950/30"
    >
      <p className="font-medium text-amber-900 dark:text-amber-200">Dato no disponible</p>
      <p className="mt-1 text-amber-800 dark:text-amber-300">{message}</p>
      {detail ? (
        <p className="mt-2 font-mono text-xs break-words text-amber-700/80 dark:text-amber-400/70">
          {detail}
        </p>
      ) : null}
      <p className="mt-2 text-xs text-amber-700 dark:text-amber-400">
        Esto no significa que no haya habido actividad: significa que no pudimos leerla.
      </p>
    </div>
  );
}

/** Renders `children` with the data, or an honest explanation instead. */
export function WithResult<T>({
  result,
  children,
}: {
  result: Result<T>;
  children: (data: T) => ReactNode;
}) {
  if (result.status === "unavailable") {
    return <Unavailable message={result.message} detail={result.detail} />;
  }
  return <>{children(result.data)}</>;
}

/** Placeholder shown while a section streams in. Deliberately not a zero. */
export function Loading({ label = "Cargando…" }: { label?: string }) {
  return (
    <div
      role="status"
      aria-live="polite"
      className="animate-pulse rounded-lg border border-slate-200 bg-slate-50 p-4 text-sm text-slate-500 dark:border-slate-800 dark:bg-slate-900/50 dark:text-slate-400"
    >
      {label}
    </div>
  );
}

/** Wraps a section so one failure cannot blank the whole page. */
export function Section({
  title,
  description,
  children,
  action,
}: {
  title: string;
  description?: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-slate-900 dark:text-slate-100">{title}</h2>
          {description ? (
            <p className="mt-0.5 text-sm text-slate-500 dark:text-slate-400">{description}</p>
          ) : null}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}
