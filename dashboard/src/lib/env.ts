/**
 * Server-only configuration.
 *
 * THE RULE THIS FILE ENFORCES: none of these values may ever reach the browser.
 * They are read exclusively from Server Components, Route Handlers and
 * middleware. Not one of them is prefixed `NEXT_PUBLIC_`, because that prefix
 * is what tells Next.js to inline a value into the client bundle — and a token
 * inlined into the bundle is a published token, whatever it is called.
 *
 * The panel talks to the backend through its own `/api/backend/*` proxy, so the
 * browser never needs the backend URL or the API token to exist client-side.
 */

import "server-only";

function required(name: string): string {
  const value = process.env[name];
  if (!value || !value.trim()) {
    // Failing loudly at the first request beats rendering a panel that shows
    // "no disponible" everywhere while the real problem is a missing variable.
    throw new Error(
      `Falta la variable de entorno ${name}. Revisa dashboard/.env.local ` +
        `(o las variables del proyecto en tu hosting).`,
    );
  }
  return value.trim();
}

/** Public base URL of the Modal backend, without a trailing slash. */
export function backendUrl(): string {
  return required("BACKEND_URL").replace(/\/+$/, "");
}

/** Shared bearer token for the backend's read endpoints. Server-side only. */
export function backendToken(): string {
  return required("DASHBOARD_API_TOKEN");
}

/** The single password that opens the panel. One clinic, one password. */
export function panelPassword(): string {
  return required("DASHBOARD_PASSWORD");
}

/** Secret used to sign the session cookie. Unrelated to the password. */
export function sessionSecret(): string {
  return required("DASHBOARD_SESSION_SECRET");
}
