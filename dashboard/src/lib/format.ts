/**
 * Formatting for people, not for machines.
 *
 * The rule shared by everything here: a value that does not exist is rendered
 * as "—", never as 0 and never as "null". A dash reads as "no hay dato"; a zero
 * reads as a measurement.
 */

import { branding } from "@/config/branding";

export function duration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  if (seconds < 60) return `${seconds} s`;
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return rest === 0 ? `${minutes} min` : `${minutes} min ${rest} s`;
}

export function percent(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${value}%`;
}

export function count(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat(branding.locale).format(value);
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return "—";
  return new Intl.DateTimeFormat(branding.locale, {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  }).format(parsed);
}

/** Human label for where a call came from. Web calls have no phone number. */
export function originLabel(origin: string): string {
  switch (origin) {
    case "web":
      return "Web";
    case "inbound":
      return "Entrante";
    case "outbound":
      return "Saliente";
    case "phone":
      return "Teléfono";
    default:
      return "—";
  }
}

export function urgencyTone(urgency: string | null | undefined): string {
  switch ((urgency || "").toLowerCase()) {
    case "urgente":
      return "bg-red-100 text-red-800 dark:bg-red-950/50 dark:text-red-300";
    case "normal":
      return "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300";
    case "baja":
      return "bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400";
    default:
      return "bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400";
  }
}
