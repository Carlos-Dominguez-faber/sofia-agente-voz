/**
 * Exchange the panel password for a session cookie.
 *
 * The password never travels beyond this handler and is never stored anywhere.
 * What the browser receives is an httpOnly cookie it cannot read from
 * JavaScript — so a cross-site script cannot lift the session, and the password
 * itself is not in the browser to be lifted.
 */

import { NextResponse } from "next/server";

import { panelPassword, sessionSecret } from "@/lib/env";
import { SESSION_COOKIE, SESSION_DURATION_MS, createSession, passwordMatches } from "@/lib/session";

/**
 * Slows down guessing without needing a store to count attempts. A person
 * typing their password does not notice; a script trying a wordlist does.
 */
const THROTTLE_MS = 600;

export async function POST(request: Request) {
  let submitted = "";
  try {
    const body = (await request.json()) as { password?: unknown };
    submitted = typeof body.password === "string" ? body.password : "";
  } catch {
    submitted = "";
  }

  await new Promise((resolve) => setTimeout(resolve, THROTTLE_MS));

  let expected: string;
  let secret: string;
  try {
    expected = panelPassword();
    secret = sessionSecret();
  } catch (error) {
    return NextResponse.json(
      { ok: false, message: (error as Error).message },
      { status: 500 },
    );
  }

  if (!submitted || !passwordMatches(submitted, expected)) {
    return NextResponse.json(
      { ok: false, message: "Contraseña incorrecta." },
      { status: 401 },
    );
  }

  const response = NextResponse.json({ ok: true, message: "Sesión iniciada." });
  response.cookies.set({
    name: SESSION_COOKIE,
    value: await createSession(secret),
    httpOnly: true, // JavaScript in the page cannot read it
    sameSite: "lax", // not sent on cross-site requests
    secure: process.env.NODE_ENV === "production", // HTTPS only once deployed
    path: "/",
    maxAge: SESSION_DURATION_MS / 1000,
  });
  return response;
}
