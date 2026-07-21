/**
 * The panel's own lock — the second of the two layers.
 *
 * The backend token stops anyone calling the API directly. It does nothing
 * about who may open this panel: on a public deployment URL, without this, any
 * visitor reads real patients' call summaries. So the panel has a password of
 * its own.
 *
 * One clinic, one password. No user table, no roles — that is the multi-user
 * auth the brief rules out, and this is not it.
 *
 * The cookie holds an expiry and an HMAC over it. Nothing secret is stored in
 * the browser: without the server's secret nobody can fabricate a valid cookie,
 * and an expired one cannot be replayed. Signing uses Web Crypto so the same
 * code runs in the proxy on the edge runtime.
 */

const encoder = new TextEncoder();

export const SESSION_COOKIE = "sofia_panel_session";

/** Eight hours: a clinic opens the panel in the morning and closes at night. */
export const SESSION_DURATION_MS = 8 * 60 * 60 * 1000;

async function hmac(secret: string, message: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const signature = await crypto.subtle.sign(
    "HMAC",
    key,
    encoder.encode(message),
  );
  return Array.from(new Uint8Array(signature))
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

/** Build a signed session value valid until `expiresAt`. */
export async function createSession(
  secret: string,
  now = Date.now(),
): Promise<string> {
  const expiresAt = now + SESSION_DURATION_MS;
  const signature = await hmac(secret, String(expiresAt));
  return `${expiresAt}.${signature}`;
}

/** True only for a well-formed, correctly signed, unexpired session. */
export async function verifySession(
  secret: string,
  value: string | undefined,
  now = Date.now(),
): Promise<boolean> {
  if (!value) return false;

  const separator = value.lastIndexOf(".");
  if (separator < 1) return false;

  const expiresAt = value.slice(0, separator);
  const signature = value.slice(separator + 1);

  const expiry = Number(expiresAt);
  if (!Number.isFinite(expiry) || expiry < now) return false;

  const expected = await hmac(secret, expiresAt);
  return timingSafeEqual(signature, expected);
}

/**
 * Compare without leaking where two strings diverge.
 *
 * A plain `===` returns as soon as it finds a difference, and the time that
 * takes tells an attacker how much of the signature they guessed right.
 */
function timingSafeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let difference = 0;
  for (let i = 0; i < a.length; i += 1) {
    difference |= a.charCodeAt(i) ^ b.charCodeAt(i);
  }
  return difference === 0;
}

/** Same constant-time comparison, for checking the submitted password. */
export function passwordMatches(submitted: string, expected: string): boolean {
  return timingSafeEqual(submitted, expected);
}
