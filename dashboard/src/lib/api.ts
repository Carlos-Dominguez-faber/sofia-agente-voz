/**
 * The only place that talks to the backend.
 *
 * Runs on the server, always. The bearer token is attached here and never
 * leaves this process: components receive data, never credentials, and the
 * browser reaches the backend only through `/api/backend/*`, which calls into
 * this module.
 *
 * Failure is a value, not an exception. Every call returns a `Result`, so a
 * section whose source is down renders "no disponible" while the rest of the
 * panel keeps working. Nothing here ever substitutes a zero for a number it
 * could not obtain — a clinic owner reads a zero as "Sofía did not work today",
 * and that lie is more expensive than an error message.
 */

import "server-only";

import { backendToken, backendUrl } from "@/lib/env";
import type {
  CallDetail,
  CallList,
  Envelope,
  Funnel,
  Metrics,
  PromptPayload,
  Result,
  ServiceStatus,
  Temperature,
} from "@/lib/types";

/** A slow backend must not hang the page forever. */
const TIMEOUT_MS = 20_000;

type FetchOptions = {
  method?: "GET" | "POST" | "PUT";
  body?: unknown;
  /** Seconds to cache. 0 disables caching, which is the default for live data. */
  revalidate?: number;
};

export async function backendFetch<T>(path: string, options: FetchOptions = {}): Promise<Result<T>> {
  const { method = "GET", body, revalidate = 0 } = options;

  let url: string;
  let token: string;
  try {
    url = `${backendUrl()}${path.startsWith("/") ? path : `/${path}`}`;
    token = backendToken();
  } catch (error) {
    // A missing environment variable is a configuration problem, and saying so
    // plainly saves an hour of debugging a panel that shows nothing.
    return { status: "unavailable", message: (error as Error).message };
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);

  try {
    const response = await fetch(url, {
      method,
      headers: {
        Authorization: `Bearer ${token}`,
        ...(body ? { "Content-Type": "application/json" } : {}),
      },
      body: body ? JSON.stringify(body) : undefined,
      signal: controller.signal,
      next: revalidate > 0 ? { revalidate } : { revalidate: 0 },
    });

    const payload = (await response.json().catch(() => null)) as Partial<Envelope<T>> | null;

    if (!payload || typeof payload !== "object") {
      return {
        status: "unavailable",
        message: "El backend respondió algo que no pude leer.",
        detail: `HTTP ${response.status}`,
      };
    }

    if (payload.ok === true && "data" in payload) {
      return { status: "ok", data: payload.data as T };
    }

    // Not every failure arrives inside our envelope. A 404 from FastAPI is
    // `{"detail": "Not Found"}` — no `ok`, no `error` — and reaching blindly
    // into `payload.error.code` turns a missing endpoint into a TypeError that
    // says nothing useful. The most common cause is a backend that has not been
    // redeployed since these endpoints were added, so the message says so.
    if (payload.ok === undefined) {
      const detail =
        typeof (payload as { detail?: unknown }).detail === "string"
          ? (payload as { detail: string }).detail
          : JSON.stringify(payload).slice(0, 200);
      return {
        status: "unavailable",
        message:
          response.status === 404
            ? "El backend no reconoce esta ruta. Puede que falte desplegar la versión con los endpoints del panel."
            : "El backend respondió en un formato inesperado.",
        detail: `HTTP ${response.status} · ${detail}`,
      };
    }

    const failure = payload as Extract<Envelope<T>, { ok: false }>;
    return {
      status: "unavailable",
      message: failure.message ?? "El backend reportó un error.",
      detail: failure.error
        ? `${failure.error.code}: ${failure.error.detail}`
        : `HTTP ${response.status}`,
    };
  } catch (error) {
    const aborted = (error as Error).name === "AbortError";
    return {
      status: "unavailable",
      message: aborted
        ? "El backend tardó demasiado en responder."
        : "No pude contactar al backend.",
      detail: (error as Error).message,
    };
  } finally {
    clearTimeout(timer);
  }
}

// --------------------------------------------------------------------------
// The reads the panel performs
// --------------------------------------------------------------------------

export const getMetrics = (days = 30) =>
  backendFetch<Metrics>(`/dashboard/metrics?days=${days}`);

export const getCalls = (days = 30, limit = 20) =>
  backendFetch<CallList>(`/dashboard/calls?days=${days}&limit=${limit}`);

export const getCallDetail = (callId: string) =>
  backendFetch<CallDetail>(`/dashboard/calls/${encodeURIComponent(callId)}`);

export const getFunnel = () => backendFetch<Funnel>("/dashboard/funnel");

export const getTemperature = () => backendFetch<Temperature>("/dashboard/leads/temperature");

export const getPrompt = () => backendFetch<PromptPayload>("/dashboard/agent/prompt");

export const getServicesStatus = () =>
  backendFetch<ServiceStatus>("/dashboard/services/status");
